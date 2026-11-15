let chart = null; const hist = { labels: [], dl: [], ul: [] }, MAX = 60;
function pushSample(ifs, sel) {
  const src = sel === 'all' ? ifs.filter(i => i.up) : ifs.filter(i => i.name === sel);
  if (!src.some(i => i.rx !== null)) return;           // still measuring: skip
  const s = k => src.reduce((a, i) => a + (i[k] || 0), 0);
  hist.labels.push(new Date().toLocaleTimeString()); hist.dl.push(+s('rx').toFixed(2)); hist.ul.push(+s('tx').toFixed(2));
  if (hist.labels.length > MAX) ['labels', 'dl', 'ul'].forEach(k => hist[k].shift());
}
function resetHist() { hist.labels.length = hist.dl.length = hist.ul.length = 0; }
function mountChart(canvas) {
  if (chart) chart.destroy();
  const ds = (l, d, c) => ({ label: l, data: d, borderColor: c, backgroundColor: c + '22', fill: true, tension: .35, pointRadius: 0, borderWidth: 2 });
  chart = new Chart(canvas, { type: 'line', data: { labels: hist.labels, datasets: [ds('Download', hist.dl, '#22d3ee'), ds('Upload', hist.ul, '#a78bfa')] },
    options: { responsive: true, maintainAspectRatio: false, animation: { duration: 300 }, interaction: { mode: 'index', intersect: false },
      scales: { x: { ticks: { color: '#8a9bb8', maxTicksLimit: 6 }, grid: { color: '#1f2c47' } }, y: { beginAtZero: true, title: { display: true, text: 'Mbps', color: '#8a9bb8' }, ticks: { color: '#8a9bb8' }, grid: { color: '#1f2c47' } } },
      plugins: { legend: { labels: { color: '#e6edf8' } } } } });
}
const updateChart = () => chart && chart.update();
