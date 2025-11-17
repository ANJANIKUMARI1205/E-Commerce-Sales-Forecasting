# app.py
import os
import io
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, render_template, request, jsonify, send_from_directory
import pandas as pd
import numpy as np
import threading
import webbrowser
import traceback

# Try to import Prophet; fallback will be used if missing
try:
    from prophet import Prophet
    PROPHET_AVAILABLE = True
except Exception:
    PROPHET_AVAILABLE = False
    from statsmodels.tsa.arima.model import ARIMA

# DB file
DB_PATH = "data.db"
UPLOAD_FOLDER = "uploads"

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# -------------------------
# DB Utilities (SQLite)
# -------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # sales table: date (ISO), product_id, qty, price, total
    c.execute('''
    CREATE TABLE IF NOT EXISTS sales (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        invoice_date TEXT,
        product_id INTEGER,
        qty INTEGER,
        price REAL,
        total REAL
    );
    ''')
    # products table
    c.execute('''
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        category TEXT,
        price REAL
    );
    ''')
    # customers table
    c.execute('''
    CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        email TEXT,
        phone TEXT,
        address TEXT,
        created_at TEXT
    );
    ''')
    conn.commit()
    conn.close()

def insert_sales_df(df):
    """
    df must have columns: InvoiceDate, ProductID (optional), Qty, Price, Total
    """
    conn = sqlite3.connect(DB_PATH)
    df.to_sql('sales', conn, if_exists='append', index=False)
    conn.close()

def insert_products_df(df):
    conn = sqlite3.connect(DB_PATH)
    df.to_sql('products', conn, if_exists='append', index=False)
    conn.close()

def insert_customers_df(df):
    conn = sqlite3.connect(DB_PATH)
    df.to_sql('customers', conn, if_exists='append', index=False)
    conn.close()

# Initialize DB on start
init_db()

# -------------------------
# Helper: read aggregated summary
# -------------------------
def get_summary():
    conn = sqlite3.connect(DB_PATH)
    df_sales = pd.read_sql_query("SELECT * FROM sales", conn, parse_dates=['invoice_date'])
    df_products = pd.read_sql_query("SELECT * FROM products", conn)
    df_customers = pd.read_sql_query("SELECT * FROM customers", conn, parse_dates=['created_at'])
    conn.close()

    # If df_sales empty, return defaults
    if df_sales.empty:
        return {
            "total_sales": 0,
            "total_orders": 0,
            "total_customers": int(len(df_customers)),
            "top_products": [],
            "monthly": []
        }

    # Normalize column names if needed
    if 'InvoiceDate' in df_sales.columns and 'invoice_date' not in df_sales.columns:
        df_sales.rename(columns={'InvoiceDate':'invoice_date'}, inplace=True)
    if 'Sales' in df_sales.columns and 'total' not in df_sales.columns:
        # some CSVs may have Sales column directly
        df_sales['total'] = df_sales['Sales']

    # Total sales
    total_sales = float(df_sales['total'].sum())
    total_orders = int(len(df_sales))
    total_customers = int(df_customers.shape[0])

    # Top products (by total)
    if 'product_id' in df_sales.columns:
        prod_agg = df_sales.groupby('product_id').agg({'total':'sum'}).reset_index().sort_values('total', ascending=False).head(10)
        top_products = []
        for _, r in prod_agg.iterrows():
            pid = int(r['product_id'])
            name_row = df_products[df_products['id']==pid]
            name = name_row['name'].values[0] if not name_row.empty else f"Product #{pid}"
            top_products.append({"product_id": pid, "description": name, "sales": float(r['total'])})
    else:
        top_products = []

    # Monthly aggregation for charts
    df_sales['invoice_date'] = pd.to_datetime(df_sales['invoice_date'])
    monthly = df_sales.set_index('invoice_date').resample('MS').agg({'total':'sum'}).reset_index()
    monthly = monthly.rename(columns={'invoice_date':'InvoiceDate','total':'Sales'})
    monthly_records = monthly.to_dict(orient='records')

    return {
        "total_sales": total_sales,
        "total_orders": total_orders,
        "total_customers": total_customers,
        "top_products": top_products,
        "monthly": monthly_records
    }

# -------------------------
# Routes: Pages
# -------------------------
@app.route('/')
def index():
    return render_template('index.html')

# Serve uploads if needed
@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# -------------------------
# Routes: Upload endpoints
# -------------------------
def safe_save_file(file_storage, prefix):
    fname = f"{prefix}__{datetime.now().strftime('%Y%m%d%H%M%S')}_{file_storage.filename}"
    path = os.path.join(app.config['UPLOAD_FOLDER'], fname)
    file_storage.save(path)
    return path

@app.route('/upload/sales', methods=['POST'])
def upload_sales():
    """
    Accepts CSV with at least: InvoiceDate, Sales (or Total or Qty & Price)
    Optionally: ProductID / Product
    """
    try:
        f = request.files.get('file')
        if not f:
            return jsonify({"error":"No file uploaded"}), 400
        path = safe_save_file(f, 'sales')
        df = pd.read_csv(path)
        # Standardize
        # Accept many naming variants
        df_columns = {c.lower():c for c in df.columns}
        colmap = {}
        # Invoice date
        for name in ['invoicedate','invoice_date','date','ds']:
            if name in df_columns:
                colmap[df_columns[name]] = 'invoice_date'
                break
        # total sales
        for name in ['sales','sale','total','amount','revenue']:
            if name in df_columns:
                colmap[df_columns[name]] = 'total'
                break
        # qty and price
        if 'qty' in df_columns:
            colmap[df_columns['qty']] = 'qty'
        if 'price' in df_columns:
            colmap[df_columns['price']] = 'price'
        # product id or product column
        for name in ['productid','product_id','product','pid','productId']:
            if name in df_columns:
                colmap[df_columns[name]] = 'product_id'
                break
        df = df.rename(columns=colmap)
        # If total missing but qty & price present, compute
        if 'total' not in df.columns and ('qty' in df.columns and 'price' in df.columns):
            df['total'] = df['qty'] * df['price']
        # Keep only columns we store (invoice_date, product_id, qty, price, total)
        keep = [c for c in ['invoice_date','product_id','qty','price','total'] if c in df.columns]
        df = df[keep]
        # Convert invoice_date to ISO string, ensure column exists name 'invoice_date'
        if 'invoice_date' in df.columns:
            df['invoice_date'] = pd.to_datetime(df['invoice_date']).dt.strftime('%Y-%m-%d')
        # Save to DB (append)
        insert_sales_df(df)
        return jsonify({"status":"ok","rows": len(df)})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error":str(e)}), 500

@app.route('/upload/products', methods=['POST'])
def upload_products():
    try:
        f = request.files.get('file')
        if not f:
            return jsonify({"error":"No file uploaded"}), 400
        path = safe_save_file(f, 'products')
        df = pd.read_csv(path)
        # Normalize columns - name, category, price
        df_columns = {c.lower():c for c in df.columns}
        colmap = {}
        for name in ['name','product','productname','product_name']:
            if name in df_columns:
                colmap[df_columns[name]] = 'name'
                break
        for name in ['category','cat']:
            if name in df_columns:
                colmap[df_columns[name]] = 'category'
                break
        for name in ['price','mrp','cost']:
            if name in df_columns:
                colmap[df_columns[name]] = 'price'
                break
        df = df.rename(columns=colmap)
        keep = [c for c in ['name','category','price'] if c in df.columns]
        df = df[keep]
        insert_products_df(df)
        return jsonify({"status":"ok","rows": len(df)})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error":str(e)}), 500

@app.route('/upload/customers', methods=['POST'])
def upload_customers():
    try:
        f = request.files.get('file')
        if not f:
            return jsonify({"error":"No file uploaded"}), 400
        path = safe_save_file(f, 'customers')
        df = pd.read_csv(path)
        df_columns = {c.lower():c for c in df.columns}
        colmap = {}
        for name in ['name','customername','customer_name']:
            if name in df_columns:
                colmap[df_columns[name]] = 'name'
                break
        for name in ['email','mail']:
            if name in df_columns:
                colmap[df_columns[name]] = 'email'
                break
        for name in ['phone','mobile']:
            if name in df_columns:
                colmap[df_columns[name]] = 'phone'
                break
        for name in ['address','addr']:
            if name in df_columns:
                colmap[df_columns[name]] = 'address'
                break
        df = df.rename(columns=colmap)
        if 'created_at' not in df.columns:
            df['created_at'] = datetime.now().strftime('%Y-%m-%d')
        keep = [c for c in ['name','email','phone','address','created_at'] if c in df.columns]
        df = df[keep]
        insert_customers_df(df)
        return jsonify({"status":"ok","rows": len(df)})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error":str(e)}), 500

# -------------------------
# Routes: Add via forms (POST)
# -------------------------
@app.route('/add/customer', methods=['POST'])
def add_customer():
    try:
        name = request.form.get('name')
        email = request.form.get('email')
        phone = request.form.get('phone')
        address = request.form.get('address')
        created_at = datetime.now().strftime('%Y-%m-%d')
        df = pd.DataFrame([{
            "name": name, "email": email, "phone": phone, "address": address, "created_at": created_at
        }])
        insert_customers_df(df)
        return jsonify({"status":"ok"})
    except Exception as e:
        return jsonify({"error":str(e)}), 500

@app.route('/add/product', methods=['POST'])
def add_product():
    try:
        pname = request.form.get('pname')
        qty = request.form.get('qty', type=int)
        price = request.form.get('price', type=float)
        # Insert product if not exists
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        # Simple add product entry (price stored)
        c.execute("INSERT INTO products (name, price) VALUES (?,?)", (pname, price))
        pid = c.lastrowid
        # Also add a sale entry for this purchase (optional)
        invoice_date = datetime.now().strftime('%Y-%m-%d')
        total = qty * price
        c.execute("INSERT INTO sales (invoice_date, product_id, qty, price, total) VALUES (?,?,?,?,?)",
                  (invoice_date, pid, qty, price, total))
        conn.commit()
        conn.close()
        return jsonify({"status":"ok"})
    except Exception as e:
        return jsonify({"error":str(e)}), 500

# -------------------------
# API: Summary for frontend
# -------------------------
@app.route('/api/summary')
def api_summary():
    try:
        s = get_summary()
        # Return monthly as simple list of objects with InvoiceDate and Sales
        return jsonify(s)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# -------------------------
# Forecasting utilities
# -------------------------
def forecast_with_prophet(df, periods=30, freq='D'):
    # df with columns ds (datetime) and y (float)
    m = Prophet()
    m.fit(df)
    future = m.make_future_dataframe(periods=periods, freq=freq)
    forecast = m.predict(future)
    return forecast

def forecast_with_arima(df, periods=30):
    # df is time series with index ds (datetime) and y
    # We'll fit an ARIMA(1,1,1) on y
    series = df.set_index('ds')['y'].astype(float)
    series = series.asfreq('D').fillna(method='ffill')
    model = ARIMA(series, order=(1,1,1))
    model_fit = model.fit()
    pred = model_fit.get_forecast(steps=periods)
    index = pd.date_range(start=series.index[-1]+pd.Timedelta(days=1), periods=periods, freq='D')
    yhat = pred.predicted_mean
    out = pd.DataFrame({'ds': index, 'yhat': yhat.values})
    return out

# -------------------------
# API: Sales Forecast
# -------------------------
@app.route('/api/forecast')
def api_forecast():
    """
    Returns forecasted daily sales for next N days (default 30).
    Also returns a short written analysis: increase/decrease %
    """
    try:
        days = int(request.args.get('days', 30))
        conn = sqlite3.connect(DB_PATH)
        df = pd.read_sql_query("SELECT invoice_date, total FROM sales", conn, parse_dates=['invoice_date'])
        conn.close()
        if df.empty:
            return jsonify({"error":"No sales data available"}), 400
        # Prepare df for model: ds, y
        df = df.rename(columns={'invoice_date':'ds','total':'y'})
        df['ds'] = pd.to_datetime(df['ds'])
        # Aggregate daily
        df = df.groupby('ds').agg({'y':'sum'}).reset_index()
        # If Prophet available:
        if PROPHET_AVAILABLE:
            forecast = forecast_with_prophet(df, periods=days)
            fc = forecast[['ds','yhat']].tail(days).rename(columns={'yhat':'y'})
        else:
            fc = forecast_with_arima(df, periods=days)
        # Simple trend estimate: compare mean of last 30 days to forecast mean of next 30 days
        recent_mean = df.tail(30)['y'].mean() if len(df)>=30 else df['y'].mean()
        forecast_mean = fc['y'].mean()
        pct_change = (forecast_mean - recent_mean) / recent_mean * 100 if recent_mean != 0 else 0
        trend = "increase" if pct_change > 0 else "decrease" if pct_change < 0 else "stable"
        analysis_text = f"Recent mean daily sales: {recent_mean:.2f}. Forecast mean for next {days} days: {forecast_mean:.2f}. Expected {trend} of {pct_change:.1f}%."
        # Return forecast as list of ds,y
        return jsonify({
            "forecast": fc.sort_values('ds').to_dict(orient='records'),
            "trend": trend,
            "pct_change": pct_change,
            "analysis": analysis_text
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# -------------------------
# API: Product Forecast
# -------------------------
@app.route('/api/product-forecast')
def api_product_forecast():
    """
    Forecast per product for next N days, returns top predictions.
    """
    try:
        days = int(request.args.get('days', 30))
        conn = sqlite3.connect(DB_PATH)
        df_sales = pd.read_sql_query("SELECT invoice_date, product_id, qty FROM sales", conn, parse_dates=['invoice_date'])
        df_products = pd.read_sql_query("SELECT * FROM products", conn)
        conn.close()
        if df_sales.empty:
            return jsonify({"error":"No sales data available"}), 400
        results = {}
        for pid in df_sales['product_id'].dropna().unique():
            pid = int(pid)
            dfp = df_sales[df_sales['product_id']==pid].copy()
            dfp = dfp.rename(columns={'invoice_date':'ds','qty':'y'})
            dfp['ds'] = pd.to_datetime(dfp['ds'])
            daily = dfp.groupby('ds').agg({'y':'sum'}).reset_index()
            if len(daily) < 3:
                continue
            # Model
            try:
                if PROPHET_AVAILABLE:
                    fc = forecast_with_prophet(daily.rename(columns={'y':'y'}), periods=days)
                    fc2 = fc[['ds','yhat']].tail(days).rename(columns={'yhat':'y'})
                else:
                    fc2 = forecast_with_arima(daily.rename(columns={'ds':'ds','y':'y'}), periods=days)
            except Exception:
                continue
            pname_row = df_products[df_products['id']==pid]
            pname = pname_row['name'].values[0] if not pname_row.empty else f"Product {pid}"
            results[pname] = {
                "product_id": pid,
                "forecast": fc2.sort_values('ds').to_dict(orient='records'),
                "next_mean": float(np.mean([r['y'] for r in fc2.to_dict('records')])),
            }
        # Sort products by next_mean descending and return top 10
        ranked = sorted(results.items(), key=lambda x: x[1]['next_mean'], reverse=True)
        ranked = [{ "product": name, **data } for name, data in ranked[:10]]
        return jsonify({"product_forecast": ranked})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error":str(e)}), 500

# -------------------------
# API: Customer Forecast & Segments
# -------------------------
@app.route('/api/customer-forecast')
def api_customer_forecast():
    try:
        days = int(request.args.get('days', 30))
        conn = sqlite3.connect(DB_PATH)
        df_sales = pd.read_sql_query("SELECT invoice_date, customer_id FROM sales", conn, parse_dates=['invoice_date'])
        conn.close()
        if df_sales.empty:
            return jsonify({"error":"No sales data available"}), 400
        # daily unique customers
        df_sales['invoice_date'] = pd.to_datetime(df_sales['invoice_date'])
        daily_cust = df_sales.groupby(pd.Grouper(key='invoice_date', freq='D')).agg({'customer_id': lambda s: len(pd.unique(s.dropna()))}).reset_index()
        daily_cust = daily_cust.rename(columns={'invoice_date':'ds','customer_id':'y'})
        daily_cust['ds'] = pd.to_datetime(daily_cust['ds'])
        if PROPHET_AVAILABLE:
            fc = forecast_with_prophet(daily_cust, periods=days)
            fc2 = fc[['ds','yhat']].tail(days).rename(columns={'yhat':'y'})
        else:
            fc2 = forecast_with_arima(daily_cust, periods=days)
        return jsonify({"customer_forecast": fc2.sort_values('ds').to_dict(orient='records')})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error":str(e)}), 500

@app.route('/api/segments')
def api_segments():
    """
    Simple RFM-style segmentation:
    - R: recency (days since last purchase)
    - F: frequency (number of purchases)
    - M: monetary (total spent)
    We'll bucket into simple groups.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        df_sales = pd.read_sql_query("SELECT invoice_date, product_id, qty, total FROM sales", conn, parse_dates=['invoice_date'])
        conn.close()

        if df_sales.empty:
            return jsonify({"segments": {}})

        # For segmentation we need customer_id ideally; fallback: product-level segmentation
        # We'll do simple temporal segmentation: last 30 days vs prior mean
        df_sales['invoice_date'] = pd.to_datetime(df_sales['invoice_date'])
        today = df_sales['invoice_date'].max()
        last_30 = df_sales[df_sales['invoice_date'] >= (today - pd.Timedelta(days=30))]
        prev_30 = df_sales[(df_sales['invoice_date'] < (today - pd.Timedelta(days=30))) & (df_sales['invoice_date'] >= (today - pd.Timedelta(days=60)))]
        a = last_30['total'].sum()
        b = prev_30['total'].sum() if not prev_30.empty else 0
        segments = {
            "last_30_sales": float(a),
            "prev_30_sales": float(b),
            "ratio": float(a / b) if b!=0 else None
        }
        # For charts we return a simple bucket of high/medium/low product sales
        prod_agg = df_sales.groupby('product_id').agg({'total':'sum'}).reset_index()
        if prod_agg.empty:
            return jsonify({"segments": segments})
        q1 = prod_agg['total'].quantile(0.33)
        q2 = prod_agg['total'].quantile(0.66)
        high = int(prod_agg[prod_agg['total'] >= q2].shape[0])
        mid = int(prod_agg[(prod_agg['total'] < q2) & (prod_agg['total'] >= q1)].shape[0])
        low = int(prod_agg[prod_agg['total'] < q1].shape[0])
        segments_chart = {"High demand": high, "Medium demand": mid, "Low demand": low}
        segments['chart'] = segments_chart
        return jsonify({"segments": segments})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error":str(e)}), 500

# -------------------------
# Auto open browser helper
# -------------------------
def start_browser():
    def _open():
        try:
            # Try common Edge locations, fallback to default browser
            candidates = [
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"
            ]
            for p in candidates:
                if os.path.exists(p):
                    webbrowser.register('edge', None, webbrowser.BackgroundBrowser(p))
                    webbrowser.get('edge').open("http://127.0.0.1:5000/")
                    return
            # else open default browser
            webbrowser.open("http://127.0.0.1:5000/")
        except Exception:
            pass
    threading.Thread(target=_open).start()

if __name__ == '__main__':
    start_browser()
    app.run(debug=True)
