const $ = (id) => document.getElementById(id);
let model = { courses: [], events: [], deadlines: [], notifications: [] };
let currentConversation = null;
let messagesLoaded = false;
let conversationListVersion = 0;
let activeResource = '';
let resourceVersion = 0;
let topicVersion = 0;
let materialReturn = 'assignments';
const sectionTitles = {
  documents: 'Документы и платежи',
  schedule: 'Учебный график',
  grades: 'Зачётная книжка',
  official: 'Официальные уведомления',
  webinars: 'Вебинары',
  news: 'Новости',
  profile: 'Профиль',
  material: 'Учебный материал',
};
const programSections = new Set(['documents', 'schedule', 'grades', 'official']);
const materialLabels = {
  mvideo: 'Видео',
  nwresource: 'Материал',
  page: 'Страница',
  book: 'Книга',
  gbooks: 'Книга',
  quiz: 'Тест',
  assign: 'Задание',
  assignpractice: 'Практическое задание',
  forum: 'Форум',
};
let programRequest;
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

async function fillPrograms(selector) {
  if (selector.options.length) return;
  if (!programRequest) programRequest = window.pywebview.api.programs();
  const result = await programRequest;
  if (!result.ok) {
    programRequest = null;
    throw Error(result.error);
  }
  if (!result.data.length) throw Error('В этой учётной записи нет доступных учебных программ');
  if (selector.options.length) return;
  for (const program of result.data) {
    const option = element('option', '', program.label || 'Программа ' + program.id);
    option.value = program.id;
    option.selected = program.active;
    selector.append(option);
  }
}

function showPage(page) {
  logAction('tab', page);
  if (page !== 'resources') sessionStorage.setItem('osek-page', page);
  document.querySelectorAll('.page').forEach(node => node.classList.toggle('hidden', node.id !== 'page-' + page));
  document.querySelectorAll('.nav').forEach(node => node.classList.toggle('active', node.dataset.page === page));
  if (page === 'curriculum') loadCurriculum();
  if (page === 'messages' && !messagesLoaded) loadConversations();
}

function renderSourceNode(value) {
  if (typeof value === 'string') return document.createTextNode(value);
  const node = element(value.tag);
  for (const [key, data] of Object.entries(value.attrs)) node.setAttribute(key, data);
  if (value.tag === 'img') node.loading = 'lazy';
  if (value.tag === 'audio' || value.tag === 'video') {
    node.controls = true;
    node.preload = 'none';
  }
  if (value.tag === 'a' && value.attrs.href) {
    node.addEventListener('click', event => {
      event.preventDefault();
      const url = new URL(value.attrs.href);
      if (activeResource === 'webinars' && url.searchParams.get('action') === 'bbb_calendar') {
        showResource('webinars', '', value.attrs.href);
        return;
      }
      openMaterial(value.attrs.href, node.textContent.trim(), 'resources');
    });
  }
  node.append(...value.children.map(renderSourceNode));
  return node;
}

async function showResource(section, title = '', url = '') {
  const version = ++resourceVersion;
  activeResource = section;
  showPage('resources');
  logAction('tab', section);
  document.querySelectorAll('[data-section]').forEach(button => {
    button.classList.toggle('active', button.dataset.section === section);
  });
  $('resource-back').classList.toggle('hidden', section !== 'material');
  $('resource-content').replaceChildren();
  $('resource-title').textContent = title || sectionTitles[section];
  $('resource-status').textContent = 'Загружаю данные СДО…';
  const needsProgram = programSections.has(section);
  const selector = $('resource-program');
  $('resource-program-label').classList.toggle('hidden', !needsProgram);
  try {
    if (needsProgram && !selector.options.length) {
      await fillPrograms(selector);
      if (version !== resourceVersion) return;
    }
    const result = await window.pywebview.api.student_section(section, needsProgram ? selector.value : '', url);
    if (version !== resourceVersion) return;
    if (!result.ok) throw Error(result.error);
    $('resource-status').textContent = '';
    if (result.data.title && !title) $('resource-title').textContent = result.data.title;
    for (const block of result.data.blocks) {
      const panel = element('article', 'panel resource-block');
      panel.append(renderSourceNode(block));
      $('resource-content').append(panel);
    }
  } catch (error) {
    if (version === resourceVersion) $('resource-status').textContent = error.message;
  }
}

function openMaterial(url, title, previousPage = 'assignments') {
  const path = new URL(url).pathname;
  if (/^\/mod\/(page|book|label|mvideo|nwresource)\//.test(path)) {
    materialReturn = previousPage === 'resources' ? activeResource : previousPage;
    showResource('material', title, url);
    return;
  }
  openSite(url);
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
    if (item.kind === 'раздел') {
      parent.append(element('h3', 'topic-section-title', item.title));
      continue;
    }
    if (kind === 'notification') {
      const details = element('details', 'notification-card');
      details.append(element('summary', '', item.title));
      details.append(element('p', 'notification-text', item.body || 'СДО не вернула текст уведомления.'));
      details.addEventListener('toggle', () => {
        if (details.open) logAction('notification', 'open');
      });
      parent.append(details);
      continue;
    }
    const row = element('div', 'item');
    row.append(element('div', 'item-icon', kind === 'event' ? '◷' : kind === 'deadline' ? '✓' : '✦'));
    const body = element('div', 'item-body');
    body.append(element('strong', '', item.title));
    const detail = item.when ? String(item.when) : item.description || '';
    if (detail) body.append(element('small', '', detail));
    row.append(body);
    if (item.url) {
      const button = element('button', '', 'Открыть ↗');
      button.addEventListener('click', () => openMaterial(item.url, item.title));
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
  renderItems($('calendar-list'), model.events, 'event');
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
  const version = ++topicVersion;
  logAction('course', course.id);
  $('assignment-heading').textContent = course.title;
  $('open-course').classList.remove('hidden');
  $('open-course').onclick = () => openSite(course.url);
  document.querySelectorAll('#assignment-courses button').forEach(node => node.classList.toggle('active', node.dataset.courseId == course.id));
  empty($('assignment-list'), 'Загружаю темы…');
  const result = await window.pywebview.api.topics(course.url);
  if (version !== topicVersion) return;
  if (!result.ok) { empty($('assignment-list'), result.error); return; }
  if (!result.items.length) { empty($('assignment-list'), 'Темы не найдены. Откройте курс во встроенном окне.'); return; }
  const topics = result.items.map(item => Object.assign({}, item, {description: materialLabels[item.kind] || ''}));
  renderItems($('assignment-list'), topics, 'deadline');
}
let curriculumLoaded = false;
async function loadCurriculum() {
  if (curriculumLoaded) return;
  const selector = $('curriculum-program');
  const list = $('curriculum-list');
  empty(list, 'Загружаю учебный план…');
  try {
    await fillPrograms(selector);
  } catch (error) {
    empty(list, error.message);
    return;
  }
  const program = selector.value;
  const result = await window.pywebview.api.curriculum(program);
  if (program !== selector.value) return;
  if (!result.ok) { empty($('curriculum-list'), result.error); return; }
  curriculumLoaded = true;
  list.replaceChildren();
  for (const item of result.items) {
    const card = element('button', 'course-card curriculum-card');
    card.append(element('div', 'course-mark', '◈'), element('strong', '', item.title));
    if (item.teacher) card.append(element('p', '', 'Преподаватель: ' + item.teacher));
    if (item.hours) card.append(element('small', '', 'Часы: ' + item.hours));
    if (item.control) card.append(element('small', '', 'Форма контроля: ' + item.control));
    if (item.grade) card.append(element('small', '', item.grade));
    card.addEventListener('click', () => {
      const course = model.courses.find(course => new URL(course.url).searchParams.get('id') === new URL(item.url).searchParams.get('id'));
      showPage('assignments');
      selectCourse(course ? {id: course.id, title: item.title, url: item.url} : {title: item.title, url: item.url});
    });
    list.append(card);
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
document.querySelectorAll('[data-section]').forEach(button => {
  button.addEventListener('click', () => showResource(button.dataset.section));
});
$('resource-program').addEventListener('change', () => showResource(activeResource));
$('curriculum-program').addEventListener('change', () => {
  curriculumLoaded = false;
  loadCurriculum();
});
$('resource-back').addEventListener('click', () => {
  if (materialReturn === 'assignments') showPage('assignments');
  else if (sectionTitles[materialReturn]) showResource(materialReturn);
});
$('login-form').addEventListener('submit', async event => {
  event.preventDefault();
  $('login-status').textContent = 'Подключаюсь к sdo.i-spo.ru';
  const result = await window.pywebview.api.login($('username').value, $('password').value);
  if (result.ok) { $('login-status').textContent = ''; signedIn(result); }
  else $('login-status').textContent = result.error || 'Не удалось войти';
});
$('refresh').addEventListener('click', refresh);
$('logout').addEventListener('click', async () => {
  await window.pywebview.api.logout();
  ++resourceVersion;
  ++topicVersion;
  ++conversationListVersion;
  programRequest = null;
  $('curriculum-program').replaceChildren();
  $('resource-program').replaceChildren();
  $('resource-content').replaceChildren();
  $('conversation-list').replaceChildren();
  $('conversation-messages').replaceChildren();
  $('message-text').value = '';
  $('people-search-input').value = '';
  $('back-to-conversations').classList.add('hidden');
  $('message-form').classList.add('hidden');
  $('conversation-title').textContent = 'Выберите переписку';
  currentConversation = null;
  messagesLoaded = false;
  curriculumLoaded = false;
  showPage('home');
  $('app').classList.add('hidden');
  $('login').classList.remove('hidden');
  $('username').value = '';
  $('password').value = '';
});
$('dark-theme').addEventListener('change', event => document.body.classList.toggle('light', !event.target.checked));
$('autostart').addEventListener('change', async event => { const result = await window.pywebview.api.set_autostart(event.target.checked); if (!result.ok) { event.target.checked = !event.target.checked; showMessage(result.error); } });
window.addEventListener('pywebviewready', init);
window.addEventListener('error', event => logAction('ui_error', event.message || 'unknown'));
window.addEventListener('unhandledrejection', () => logAction('ui_error', 'promise_rejected'));
document.addEventListener('click', event => { const button = event.target.closest('button'); if (button?.id) logAction('button', button.id); });

async function loadConversations() {
  const version = ++conversationListVersion;
  $('back-to-conversations').classList.add('hidden');
  empty($('conversation-list'), 'Загружаю переписки…');
  const result = await window.pywebview.api.conversations();
  if (version !== conversationListVersion) return;
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

$('people-search-form').addEventListener('submit', async event => {
  event.preventDefault();
  const version = ++conversationListVersion;
  const list = $('conversation-list');
  $('back-to-conversations').classList.remove('hidden');
  empty(list, 'Ищу собеседников…');
  const result = await window.pywebview.api.search_people($('people-search-input').value);
  if (version !== conversationListVersion) return;
  if (!result.ok) {
    empty(list, result.error);
    return;
  }
  list.replaceChildren();
  for (const person of result.data) {
    const row = element('button', 'conversation-row', person.name);
    row.addEventListener('click', () => openConversation(person));
    list.append(row);
  }
  if (!result.data.length) empty(list, 'СДО не нашла собеседников.');
});
$('back-to-conversations').addEventListener('click', () => {
  $('people-search-input').value = '';
  loadConversations();
});

async function openConversation(item) {
  currentConversation = item;
  logAction('message_open', item.id);
  $('conversation-title').textContent = item.name;
  $('message-form').classList.add('hidden');
  if (!item.id) {
    empty($('conversation-messages'), item.canMessage ? 'Новая переписка' : 'СДО запретила отправку сообщений этому собеседнику.');
    $('message-form').classList.toggle('hidden', !item.canMessage);
    return;
  }
  empty($('conversation-messages'), 'Загружаю сообщения…');
  const result = await window.pywebview.api.conversation_messages(item.id);
  if (currentConversation !== item) return;
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
  const conversation = currentConversation;
  $('message-form').querySelector('button').disabled = true;
  let result;
  try {
    result = conversation.id
      ? await window.pywebview.api.send_message(conversation.id, text)
      : await window.pywebview.api.send_to_user(conversation.userId, text);
  } finally {
    $('message-form').querySelector('button').disabled = false;
  }
  logAction('message_send', result.ok ? 'success' : 'failed');
  if (!result.ok) { showMessage(result.error); return; }
  if (currentConversation !== conversation) return;
  $('message-text').value = '';
  if (conversation.id) {
    await openConversation(conversation);
  } else {
    empty($('conversation-messages'), 'СДО подтвердила отправку. Новая беседа появится в списке переписок.');
    await loadConversations();
  }
});
