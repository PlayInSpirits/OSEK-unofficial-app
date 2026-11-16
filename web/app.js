const $ = (id) => document.getElementById(id);
let model = { courses: [], events: [], deadlines: [], notifications: [] };

function showMessage(text) {
  $('message').textContent = text;
  $('message').classList.toggle('hidden', !text);
}
function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = String(text);
  return node;
}
function empty(parent, text) { parent.replaceChildren(element('p', 'empty', text)); }
function showPage(page) {
  document.querySelectorAll('.page').forEach(node => node.classList.toggle('hidden', node.id !== 'page-' + page));
  document.querySelectorAll('.nav').forEach(node => node.classList.toggle('active', node.dataset.page === page));
}
async function openSite(url) {
  const result = await window.pywebview.api.open_site(url);
  if (!result.ok) showMessage(result.error || 'Не удалось открыть страницу');
}
function renderItems(parent, items, kind) {
  parent.replaceChildren();
  if (!items.length) { empty(parent, kind === 'deadline' ? 'Сроки пока недоступны. Проверьте задания в разделе «Работы».' : 'Пока ничего нет.'); return; }
  for (const item of items) {
    const row = element('div', 'item');
    row.append(element('div', 'item-icon', kind === 'event' ? '◷' : kind === 'deadline' ? '✓' : '✦'));
    const body = element('div', 'item-body');
    body.append(element('strong', '', item.title));
    const detail = item.when ? String(item.when) : item.description || '';
    if (detail) body.append(element('small', '', detail));
    row.append(body);
    if (item.url) {
      const button = element('button', '', 'Открыть ↗');
      button.addEventListener('click', () => openSite(item.url));
      row.append(button);
    }
    parent.append(row);
  }
}
function render() {
  $('count-courses').textContent = model.courses.length;
  $('count-events').textContent = model.events.length;
  $('count-notifications').textContent = model.notifications.length;
  renderItems($('home-events'), model.events.slice(0, 6), 'event');
  renderItems($('home-deadlines'), model.deadlines.slice(0, 6), 'deadline');
  renderItems($('notifications-list'), model.notifications, 'notification');
  const cards = $('courses-list'); cards.replaceChildren();
  const picker = $('assignment-courses'); picker.replaceChildren();
  for (const course of model.courses) {
    const card = element('button', 'course-card');
    card.append(element('div', 'course-mark', '▤'), element('strong', '', course.title), element('small', '', 'Смотреть работы →'));
    card.addEventListener('click', () => { showPage('assignments'); selectCourse(course); });
    cards.append(card);
    const pick = element('button', '', course.title);
    pick.addEventListener('click', () => selectCourse(course));
    pick.dataset.courseId = course.id;
    picker.append(pick);
  }
  if (!model.courses.length) empty(cards, 'Курсы не найдены. Нажмите «Обновить».');
}
async function selectCourse(course) {
  $('assignment-heading').textContent = course.title;
  $('open-course').classList.remove('hidden');
  $('open-course').onclick = () => openSite(course.url);
  document.querySelectorAll('#assignment-courses button').forEach(node => node.classList.toggle('active', node.dataset.courseId == course.id));
  empty($('assignment-list'), 'Загружаю работы…');
  const result = await window.pywebview.api.assignments(course.url);
  if (!result.ok) { empty($('assignment-list'), result.error); return; }
  if (!result.items.length) { empty($('assignment-list'), 'На странице курса нет доступных ссылок на работы. Откройте курс во встроенном окне.'); return; }
  renderItems($('assignment-list'), result.items.map(item => ({...item, description: 'Открыть форму сдачи во встроенном окне СДО'})), 'deadline');
}
async function refresh() {
  showMessage('');
  $('refresh').disabled = true;
  const result = await window.pywebview.api.refresh();
  $('refresh').disabled = false;
  if (!result.ok) { showMessage(result.error || 'Не удалось обновить данные'); return; }
  model = result.data; render();
}
function signedIn(result) {
  model = result.data;
  $('account-name').textContent = result.username;
  $('login').classList.add('hidden'); $('app').classList.remove('hidden');
  $('password').value = '';
  render();
}
async function init() {
  const result = await window.pywebview.api.bootstrap();
  if (result.ok) signedIn(result);
  else $('username').value = result.username || '';
  const settings = await window.pywebview.api.settings();
  $('autostart').checked = !!settings.autostart;
}
document.querySelectorAll('[data-page]').forEach(button => button.addEventListener('click', () => showPage(button.dataset.page)));
$('login-form').addEventListener('submit', async event => {
  event.preventDefault();
  $('login-status').textContent = 'Подключаюсь к СДО…';
  const result = await window.pywebview.api.login($('username').value, $('password').value);
  if (result.ok) { $('login-status').textContent = ''; signedIn(result); }
  else $('login-status').textContent = result.error || 'Не удалось войти';
});
$('refresh').addEventListener('click', refresh);
$('site').addEventListener('click', () => openSite('https://sdo.i-spo.ru/my/'));
$('logout').addEventListener('click', async () => { await window.pywebview.api.logout(); $('app').classList.add('hidden'); $('login').classList.remove('hidden'); $('username').value = ''; $('password').value = ''; });
$('dark-theme').addEventListener('change', event => document.body.classList.toggle('light', !event.target.checked));
$('autostart').addEventListener('change', async event => { const result = await window.pywebview.api.set_autostart(event.target.checked); if (!result.ok) { event.target.checked = !event.target.checked; showMessage(result.error); } });
window.addEventListener('pywebviewready', init);
