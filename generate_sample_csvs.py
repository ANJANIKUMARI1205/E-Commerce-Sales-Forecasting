# tools/generate_sample_csvs.py
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import os
os.makedirs('../uploads', exist_ok=True)

def gen_sales(days=180):
    dates = [datetime.now().date() - timedelta(days=x) for x in range(days)][::-1]
    products = [1,2,3,4,5]
    rows = []
    for d in dates:
        for p in products:
            qty = max(0, int(np.random.poisson(5)))
            price = float(100 + p*10)
            total = qty * price
            if qty>0:
                rows.append({'InvoiceDate': d.strftime('%Y-%m-%d'), 'product_id': p, 'qty': qty, 'price': price, 'total': total})
    df = pd.DataFrame(rows)
    df.to_csv('../uploads/sample_sales.csv', index=False)
    print("sample_sales.csv generated")

def gen_products():
    rows = [
        {'name':'Product A','category':'Electronics','price':120.0},
        {'name':'Product B','category':'Home','price':95.0},
        {'name':'Product C','category':'Toys','price':60.0},
        {'name':'Product D','category':'Books','price':30.0},
        {'name':'Product E','category':'Clothing','price':80.0}
    ]
    pd.DataFrame(rows).to_csv('../uploads/sample_products.csv', index=False)
    print("sample_products.csv generated")

def gen_customers(n=100):
    rows=[]
    for i in range(n):
        rows.append({'name':f'Customer {i+1}','email':f'user{i+1}@example.com','phone':f'999000{i+1:03}','address':'City','created_at':(datetime.now()-timedelta(days=np.random.randint(0,365))).strftime('%Y-%m-%d')})
    pd.DataFrame(rows).to_csv('../uploads/sample_customers.csv', index=False)
    print("sample_customers.csv generated")

if __name__ == '__main__':
    gen_sales()
    gen_products()
    gen_customers()
