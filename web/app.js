const $ = (id) => document.getElementById(id);
let model = { courses: [], events: [], deadlines: [], notifications: [] };
let currentConversation = null;
let messagesLoaded = false;
const logAction = (kind, detail = '') => { const task = window.pywebview?.api?.log_event(kind, String(detail)); if (task?.catch) task.catch(() => {}); };

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
  logAction('tab', page);
  sessionStorage.setItem('osek-page', page);
  document.querySelectorAll('.page').forEach(node => node.classList.toggle('hidden', node.id !== 'page-' + page));
  document.querySelectorAll('.nav').forEach(node => node.classList.toggle('active', node.dataset.page === page));
  if (page === 'curriculum') loadCurriculum();
  if (page === 'messages' && !messagesLoaded) loadConversations();
}
async function openSite(url) {
  logAction('course', new URL(url).pathname);
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
      if (kind === 'notification') { row.classList.add('clickable'); row.addEventListener('click', () => { logAction('notification', 'open'); openSite(item.url); }); button.remove(); }
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
    card.append(element('div', 'course-mark', '▤'), element('strong', '', course.title), element('small', '', 'Смотреть темы →'));
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
  logAction('course', course.id);
  $('assignment-heading').textContent = course.title;
  $('open-course').classList.remove('hidden');
  $('open-course').onclick = () => openSite(course.url);
  document.querySelectorAll('#assignment-courses button').forEach(node => node.classList.toggle('active', node.dataset.courseId == course.id));
  empty($('assignment-list'), 'Загружаю темы…');
  const result = await window.pywebview.api.topics(course.url);
  if (!result.ok) { empty($('assignment-list'), result.error); return; }
  if (!result.items.length) { empty($('assignment-list'), 'Темы не найдены. Откройте курс во встроенном окне.'); return; }
  renderItems($('assignment-list'), result.items.map(item => ({...item, description: item.kind})), 'deadline');
}
let curriculumLoaded = false;
async function loadCurriculum() {
  if (curriculumLoaded) return;
  empty($('curriculum-list'), 'Загружаю учебный план…');
  const result = await window.pywebview.api.curriculum();
  if (!result.ok) { empty($('curriculum-list'), result.error); return; }
  curriculumLoaded = true;
  const list = $('curriculum-list'); list.replaceChildren();
  for (const item of result.items) {
    const card = element('button', 'course-card curriculum-card');
    card.append(element('div', 'course-mark', '◈'), element('strong', '', item.title));
    if (item.teacher) card.append(element('p', '', 'Преподаватель: ' + item.teacher));
    if (item.hours) card.append(element('small', '', 'Часы: ' + item.hours));
    if (item.control) card.append(element('small', '', 'Форма контроля: ' + item.control));
    card.addEventListener('click', () => openSite(item.url)); list.append(card);
  }
  if (!result.items.length) empty(list, 'Учебный план пока недоступен.');
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
  const name = result.data.profile?.name || result.username;
  $('account-name').textContent = name;
  $('top-account-name').textContent = name;
  const avatar = result.data.profile?.avatar;
  $('account-avatar').replaceChildren();
  if (avatar) { const img = element('img'); img.src = avatar; img.alt = ''; $('account-avatar').append(img); }
  else $('account-avatar').textContent = name.charAt(0).toUpperCase();
  $('login').classList.add('hidden'); $('app').classList.remove('hidden');
  $('password').value = '';
  render();
  const lastPage = sessionStorage.getItem('osek-page');
  if (lastPage && document.getElementById('page-' + lastPage)) showPage(lastPage);
}
async function init() {
  const result = await window.pywebview.api.bootstrap();
  $('username').value = result.username || '';
  if (result.saved) {
    $('resume-state').classList.remove('hidden'); $('login-form').classList.add('hidden');
    const resumed = await window.pywebview.api.resume();
    $('resume-state').classList.add('hidden'); $('login-form').classList.remove('hidden');
    if (resumed.ok) signedIn(resumed);
    else $('login-status').textContent = resumed.error || 'Не удалось войти автоматически';
  }
  const settings = await window.pywebview.api.settings();
  $('autostart').checked = !!settings.autostart;
  $('log-path').textContent = await window.pywebview.api.get_log_path();
}
document.querySelectorAll('[data-page]').forEach(button => button.addEventListener('click', () => showPage(button.dataset.page)));
document.querySelectorAll('[data-site]').forEach(button => button.addEventListener('click', () => openSite('https://sdo.i-spo.ru' + button.dataset.site)));
$('login-form').addEventListener('submit', async event => {
  event.preventDefault();
  $('login-status').textContent = 'Подключаюсь к sdo.i-spo.ru';
  const result = await window.pywebview.api.login($('username').value, $('password').value);
  if (result.ok) { $('login-status').textContent = ''; signedIn(result); }
  else $('login-status').textContent = result.error || 'Не удалось войти';
});
$('refresh').addEventListener('click', refresh);
$('logout').addEventListener('click', async () => { await window.pywebview.api.logout(); $('app').classList.add('hidden'); $('login').classList.remove('hidden'); $('username').value = ''; $('password').value = ''; });
$('dark-theme').addEventListener('change', event => document.body.classList.toggle('light', !event.target.checked));
$('autostart').addEventListener('change', async event => { const result = await window.pywebview.api.set_autostart(event.target.checked); if (!result.ok) { event.target.checked = !event.target.checked; showMessage(result.error); } });
window.addEventListener('pywebviewready', init);
window.addEventListener('error', event => logAction('ui_error', event.message || 'unknown'));
window.addEventListener('unhandledrejection', () => logAction('ui_error', 'promise_rejected'));
document.addEventListener('click', event => { const button = event.target.closest('button'); if (button?.id) logAction('button', button.id); });

async function loadConversations() {
  empty($('conversation-list'), 'Загружаю переписки…');
  const result = await window.pywebview.api.conversations();
  if (!result.ok) { empty($('conversation-list'), result.error); return; }
  messagesLoaded = true;
  const list = $('conversation-list'); list.replaceChildren();
  for (const item of result.items) {
    const row = element('button', 'conversation-row', item.name + (item.unread ? ` · ${item.unread} новых` : ''));
    row.addEventListener('click', () => openConversation(item));
    list.append(row);
  }
  if (!result.items.length) empty(list, 'Переписок пока нет.');
}
async function openConversation(item) {
  currentConversation = item;
  logAction('message_open', item.id);
  $('conversation-title').textContent = item.name;
  empty($('conversation-messages'), 'Загружаю сообщения…');
  const result = await window.pywebview.api.conversation_messages(item.id);
  if (!result.ok) { empty($('conversation-messages'), result.error); return; }
  const box = $('conversation-messages'); box.replaceChildren();
  for (const message of result.items) {
    box.append(element('div', 'message-bubble' + (message.mine ? ' own' : ''), message.text));
  }
  if (!result.items.length) empty(box, 'Сообщений пока нет.');
  $('message-form').classList.remove('hidden');
  box.scrollTop = box.scrollHeight;
}
$('message-form').addEventListener('submit', async event => {
  event.preventDefault();
  if (!currentConversation) return;
  const text = $('message-text').value.trim();
  if (!text) return;
  $('message-form').querySelector('button').disabled = true;
  const result = await window.pywebview.api.send_message(currentConversation.id, text);
  $('message-form').querySelector('button').disabled = false;
  logAction('message_send', result.ok ? 'success' : 'failed');
  if (!result.ok) { showMessage(result.error); return; }
  $('message-text').value = '';
  await openConversation(currentConversation);
});
