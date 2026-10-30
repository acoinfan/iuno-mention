const $ = id => document.getElementById(id);
const FIELDS = ['remote', 'port', 'quiet', 'per_session', 'per_all', 'urgent_per_night', 'dedupe_min', 'text_max', 'link'];

function draw(s) {
  $('err').textContent = s.error || '';
  $('state').textContent = !s.settings.on ? '没打开' : s.online ? (s.paused ? '连着，但你在 QQ 上暂停了' : '连着 remote')
    : '没连上 remote，隔 30 秒重试' ;
  if (s.outbox || s.held) $('state').textContent += `（排着 ${s.outbox} 封，静默时段存着 ${s.held} 封）`;
  $('on').checked = s.settings.on;
  for (const k of FIELDS) if (document.activeElement !== $(k)) $(k).value = s.settings[k] ?? '';
  const box = $('keybox');
  box.replaceChildren();
  if (s.public_key) {
    const tip = document.createElement('p');
    tip.className = 'muted';
    tip.textContent = `这台机器在 remote 上叫 ${s.client}。在 remote 的 iuno-mention 仓库里运行这一条登记它（已经登记过就不用了）：`;
    const code = document.createElement('code');
    code.textContent = s.add_client;
    box.append(tip, code);
  } else {
    const b = document.createElement('button');
    b.textContent = '生成钥匙';
    b.onclick = () => call('keygen');
    box.append(b);
  }
  $('log').replaceChildren(...s.log.map(x => {
    const li = document.createElement('li');
    li.textContent = `${new Date(x.at * 1000).toLocaleString()} ${x.box}：${x.what} — ${String(x.text).slice(0, 60)}`;
    return li;
  }));
}
async function call(path, body) {
  try {
    draw(await viewer.api(path, body === undefined ? { method: 'POST' } : { method: 'POST', body }));
  } catch (e) { $('err').textContent = e.message; }
}
$('save').onclick = () => {
  const body = { on: $('on').checked };
  for (const k of FIELDS) body[k] = $(k).type === 'number' ? Number($(k).value) : $(k).value;
  call('settings', body);
};
$('test').onclick = () => call('test');
viewer.api('status').then(draw).catch(e => { $('err').textContent = e.message; });
setInterval(() => viewer.api('status').then(draw).catch(() => {}), 5000);
