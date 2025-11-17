// static/js/main.js

// small helper
async function fetchJSON(path, opts = {}) {
  const r = await fetch(path, opts);
  return r.json();
}

function currency(x) {
  if (x === undefined || x === null) return '₹0';
  return '₹' + (Math.round(x * 100) / 100).toLocaleString();
}

// Draw bar chart
function drawChart(canvasId, labels, dataArr, title) {
  const ctx = document.getElementById(canvasId).getContext('2d');
  if (window[canvasId]) window[canvasId].destroy();

  window[canvasId] = new Chart(ctx, {
    type: 'bar',
    data: { labels: labels, datasets: [{ label: title, data: dataArr }] },
    options: { responsive: true }
  });
}

// Draw line + forecast chart
function drawLineWithForecast(canvasId, histLabels, histValues, fcLabels, fcValues) {
  const labels = histLabels.concat(fcLabels);
  const ctx = document.getElementById(canvasId).getContext('2d');

  if (window[canvasId]) window[canvasId].destroy();

  window[canvasId] = new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [
        {
          label: 'Historical',
          data: histValues,
          fill: false,
          borderColor: 'blue',
          tension: 0.2
        },
        {
          label: 'Forecast',
          data: Array(histValues.length).fill(null).concat(fcValues),
          borderDash: [5, 5],
          borderColor: 'orange',
          fill: false,
          tension: 0.2
        }
      ]
    },
    options: { responsive: true }
  });
}

// Load summary data (sales, orders, customers, top products)
async function loadSummary() {
  const data = await fetchJSON('/api/summary');
  if (data.error) {
    console.error(data.error);
    return;
  }

  document.getElementById('total-sales').innerText = currency(data.total_sales || 0);
  document.getElementById('total-orders').innerText = data.total_orders || 0;
  document.getElementById('total-customers').innerText = data.total_customers || 0;

  const ul = document.getElementById('top-products');
  ul.innerHTML = '';
  (data.top_products || []).forEach(p => {
    const li = document.createElement('li');
    li.innerText = `${p.description || p.Description || 'Product'}: ${currency(p.sales || p.Sales || 0)}`;
    ul.appendChild(li);
  });

  const monthlyLabels = (data.monthly || []).map(m => m.InvoiceDate);
  const monthlyValues = (data.monthly || []).map(m => m.Sales);
  drawChart('monthly-chart', monthlyLabels, monthlyValues, 'Monthly Sales');
}

// Load forecast and product forecasts
async function loadForecasts() {
  const res = await fetchJSON('/api/forecast?days=30');
  if (res.error) {
    console.error(res.error);
    return;
  }
  // historical for chart
  const summary = await fetchJSON('/api/summary');
  const histLabels = (summary.monthly || []).map(m => m.InvoiceDate);
  const histValues = (summary.monthly || []).map(m => m.Sales);
  // the forecast endpoint is daily; convert to labels and values
  const fcLabels = res.forecast.map(f => f.ds.split('T')[0]);
  const fcValues = res.forecast.map(f => parseFloat(f.y));
  // For monthly chart we simply plot monthly historic; for sales chart we create a combined view using daily forecast appended to most recent monthly date
  // Use drawLineWithForecast: hist is monthly -> keep as is but to align lengths, we'll show last N months as labels (approx)
  // Simpler: draw historical monthly + forecast daily appended as separate labels
  drawLineWithForecast('sales-chart', histLabels, histValues, fcLabels, fcValues);

  // Show trend and analysis
  document.getElementById('forecast-trend').innerText = res.trend ? res.trend.toUpperCase() : '-';
  if (res.analysis) document.getElementById('forecast-analysis').innerText = res.analysis;

  // Product forecast
  const prodRes = await fetchJSON('/api/product-forecast?days=30');
  const ul2 = document.getElementById('product-predictions');
  ul2.innerHTML = '';
  if (!prodRes.error && prodRes.product_forecast) {
    prodRes.product_forecast.forEach(p=>{
      const li = document.createElement('li');
      li.innerText = `${p.product} — predicted next mean qty: ${Math.round(p.next_mean)}`;
      ul2.appendChild(li);
    });
  }

  // Add AI Analysis summary
  const aiBox = document.getElementById('ai-analysis');
  let analysisHtml = `<p><strong>Forecast summary:</strong> ${res.analysis || ''}</p>`;
  if (prodRes.product_forecast && prodRes.product_forecast.length>0) {
    analysisHtml += `<p><strong>Top predicted products:</strong> ${prodRes.product_forecast.slice(0,3).map(x=>x.product).join(', ')}</p>`;
  }
  aiBox.innerHTML = analysisHtml;
}

// Load segments
async function loadSegments() {
  const d = await fetchJSON('/api/segments');
  if (d.error) {
    console.error(d.error);
    return;
  }
  const seg = d.segments;
  const labels = seg && seg.chart ? Object.keys(seg.chart) : ['No Data'];
  const values = seg && seg.chart ? Object.values(seg.chart) : [1];

  const ctx = document.getElementById('segments-chart').getContext('2d');
  if (window['segments-chart']) window['segments-chart'].destroy();

  window['segments-chart'] = new Chart(ctx, {
    type: 'pie',
    data: { labels: labels, datasets: [{ data: values }] },
    options: { responsive: true }
  });
}

document.addEventListener('DOMContentLoaded', function () {
  // (NOTE: removed the conflicting theme-switcher using CSS variables here)
  // Wire up buttons & uploads
  document.getElementById('upload-sales').addEventListener('click', async ()=>{
    const f = document.getElementById('sales-file').files[0];
    if (!f) { document.getElementById('sales-upload-msg').innerText = "Choose a CSV first."; return; }
    const fd = new FormData(); fd.append('file', f);
    document.getElementById('sales-upload-msg').innerText = "Uploading...";
    const res = await fetchJSON('/upload/sales', { method: 'POST', body: fd });
    document.getElementById('sales-upload-msg').innerText = res.error ? ("Error: " + res.error) : `Uploaded ${res.rows} rows.`;
    await refreshAll();
  });

  document.getElementById('upload-products').addEventListener('click', async ()=>{
    const f = document.getElementById('products-file').files[0];
    if (!f) { document.getElementById('products-upload-msg').innerText = "Choose a CSV first."; return; }
    const fd = new FormData(); fd.append('file', f);
    document.getElementById('products-upload-msg').innerText = "Uploading...";
    const res = await fetchJSON('/upload/products', { method: 'POST', body: fd });
    document.getElementById('products-upload-msg').innerText = res.error ? ("Error: " + res.error) : `Uploaded ${res.rows} rows.`;
    await refreshAll();
  });

  document.getElementById('upload-customers').addEventListener('click', async ()=>{
    const f = document.getElementById('customers-file').files[0];
    if (!f) { document.getElementById('customers-upload-msg').innerText = "Choose a CSV first."; return; }
    const fd = new FormData(); fd.append('file', f);
    document.getElementById('customers-upload-msg').innerText = "Uploading...";
    const res = await fetchJSON('/upload/customers', { method: 'POST', body: fd });
    document.getElementById('customers-upload-msg').innerText = res.error ? ("Error: " + res.error) : `Uploaded ${res.rows} rows.`;
    await refreshAll();
  });

  document.getElementById('customer-form').addEventListener('submit', async (e)=>{
    e.preventDefault();
    const form = e.target;
    const fd = new FormData(form);
    const res = await fetchJSON('/add/customer', { method:'POST', body: fd });
    if (res.error) {
      document.getElementById('form-success').style.display='block';
      document.getElementById('form-success').classList.remove('text-success');
      document.getElementById('form-success').classList.add('text-danger');
      document.getElementById('form-success').innerText = "Error: " + res.error;
    } else {
      document.getElementById('form-success').style.display='block';
      document.getElementById('form-success').classList.remove('text-danger');
      document.getElementById('form-success').classList.add('text-success');
      document.getElementById('form-success').innerText = "Customer added.";
      form.reset();
      await refreshAll();
    }
  });

  document.getElementById('product-form').addEventListener('submit', async (e)=>{
    e.preventDefault();
    const form = e.target;
    const fd = new FormData(form);
    const res = await fetchJSON('/add/product', { method:'POST', body: fd });
    if (res.error) {
      document.getElementById('product-success').style.display='block';
      document.getElementById('product-success').classList.remove('text-success');
      document.getElementById('product-success').classList.add('text-danger');
      document.getElementById('product-success').innerText = "Error: " + res.error;
    } else {
      document.getElementById('product-success').style.display='block';
      document.getElementById('product-success').classList.remove('text-danger');
      document.getElementById('product-success').classList.add('text-success');
      document.getElementById('product-success').innerText = "Product & sale recorded.";
      form.reset();
      await refreshAll();
    }
  });

  document.getElementById('reload-summary').addEventListener('click', refreshAll);

  // Start initial load
  refreshAll();
});

async function refreshAll(){
  await loadSummary();
  await loadForecasts();
  await loadSegments();
}

// THEME SWITCHER (keeps only the class-based switcher so it matches styles.css)
function applyTheme(theme) {
    document.body.className = "";
    document.body.classList.add(`theme-${theme}`);
}

document.getElementById("theme-selector").addEventListener("change", function () {
    applyTheme(this.value);
});
