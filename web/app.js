const $ = (id) => document.getElementById(id);
let model = { courses: [], events: [], deadlines: [], notifications: [] };
let currentConversation = null;
let messagesLoaded = false;
let conversationListVersion = 0;
let activeResource = '';
let resourceVersion = 0;
let topicVersion = 0;
let readerVersion = 0;
let libraryPage = 0;
let libraryQuery = '';
const readerHistory = [];
const imageObserver = new IntersectionObserver(entries => {
  for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    const image = entry.target;
    imageObserver.unobserve(image);
    loadCourseImage(image);
  }
}, {rootMargin: '250px'});
const sectionTitles = {
  documents: 'Документы и платежи',
  schedule: 'Учебный график',
  grades: 'Зачётная книжка',
  official: 'Официальные уведомления',
  webinars: 'Вебинары',
  library: 'Вебинары',
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
  assignauto: 'Работа с загрузкой ответа',
  assignpractice: 'Практическое задание',
  forum: 'Форум',
};
let programRequest;
let accountVersion = 0;
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

async function getPrograms() {
  if (!programRequest) programRequest = window.pywebview.api.programs();
  const result = await programRequest;
  if (!result.ok) {
    programRequest = null;
    throw Error(result.error);
  }
  if (!result.data.length) throw Error('В этой учётной записи нет доступных учебных программ');
  return result.data;
}

async function fillPrograms(selector) {
  if (selector.options.length) return;
  const programs = await getPrograms();
  if (selector.options.length) return;
  for (const program of programs) {
    const option = element('option', '', program.label || 'Программа ' + program.id);
    option.value = program.id;
    option.selected = program.active;
    selector.append(option);
  }
}

function showPage(page, program = '') {
  logAction('tab', page);
  if (page !== 'resources' && page !== 'reader') sessionStorage.setItem('osek-page', page);
  if (page !== 'reader') {
    readerHistory.length = 0;
    ++readerVersion;
    $('reader-content').replaceChildren();
  }
  document.querySelectorAll('.page').forEach(node => node.classList.toggle('hidden', node.id !== 'page-' + page));
  document.querySelectorAll('.nav').forEach(node => node.classList.toggle('active', node.dataset.page === page));
  document.querySelector('main.content').scrollTop = 0;
  if (page === 'curriculum') loadCurriculum(program);
  if (page === 'messages' && !messagesLoaded) loadConversations();
}

function internalLink(title, action) {
  const link = element('a', 'internal-link', title);
  link.href = '#';
  link.addEventListener('click', event => {
    event.preventDefault();
    action();
  });
  return link;
}

async function loadCourseImage(image) {
  if (!image.isConnected) return;
  try {
    const result = await window.pywebview.api.course_image(image.dataset.sdoImage);
    if (!result.ok) throw Error(result.error);
    if (image.isConnected) image.src = result.data;
  } catch (error) {
    image.title = error.message;
  }
}

function renderSourceNode(value) {
  if (typeof value === 'string') return document.createTextNode(value);
  const node = element(value.tag);
  for (const [key, data] of Object.entries(value.attrs)) {
    if (key === 'href') continue;
    if (key === 'src' && value.tag === 'img' && new URL(data).pathname.startsWith('/pluginfile.php/')) {
      node.dataset.sdoImage = data;
      imageObserver.observe(node);
      continue;
    }
    node.setAttribute(key, data);
  }
  if (value.tag === 'img') node.loading = 'lazy';
  if (value.tag === 'audio' || value.tag === 'video') {
    node.controls = true;
    node.preload = 'none';
  }
  if (value.tag === 'a' && value.attrs.href) {
    node.href = '#';
    node.addEventListener('click', event => {
      event.preventDefault();
      openMaterial(value.attrs.href, node.textContent.trim());
    });
  }
  node.append(...value.children.map(renderSourceNode));
  return node;
}

async function showResource(section, title = '', url = '', program = '') {
  const version = ++resourceVersion;
  activeResource = section;
  showPage('resources');
  logAction('tab', section);
  document.querySelectorAll('[data-section]').forEach(button => {
    button.classList.toggle('active', button.dataset.section === section || section === 'webinars' && button.dataset.section === 'library');
  });
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
    if (needsProgram && program) selector.value = program;
    const api = window.pywebview.api;
    const result = section === 'library'
      ? await api.recordings(libraryPage, libraryQuery)
      : await api.student_section(section, needsProgram ? selector.value : '', url);
    if (version !== resourceVersion) return;
    if (!result.ok) throw Error(result.error);
    $('resource-status').textContent = '';
    if (result.data.title && !title) $('resource-title').textContent = result.data.title;
    if (section === 'profile') renderProfile(result.data);
    else if (section === 'webinars') renderWebinars(result.data);
    else if (section === 'library') renderLibrary(result.data);
    else appendBlocks($('resource-content'), result.data.blocks);
  } catch (error) {
    if (version === resourceVersion) $('resource-status').textContent = error.message;
  }
}

function appendBlocks(target, blocks) {
  for (const block of blocks) {
    const panel = element('article', 'panel resource-block');
    if (block.tag === 'img') panel.classList.add('reader-book-page');
    panel.append(renderSourceNode(block));
    target.append(panel);
  }
}

function openMaterial(url, title) {
  const address = new URL(url);
  if (address.pathname === '/calendar/view.php' && address.searchParams.get('view') === 'day') {
    openReader('calendar_day', title, url);
    return;
  }
  if (address.hostname === 'sdo.vgaps.ru' && address.pathname === '/library.php') {
    openReader('recording', title, url);
    return;
  }
  const isPDF = address.pathname.endsWith('.pdf') || address.pathname === '/report/studybook/getpdf.php'
    || (address.pathname === '/mod/gbooks/view.php' && address.searchParams.has('pdf'));
  openReader(isPDF ? 'pdf' : 'material', title, url);
}

function openReader(section, title, url) {
  const current = document.querySelector('.page:not(.hidden)');
  const previous = readerHistory.at(-1);
  if (previous) previous.scroll = document.querySelector('main.content').scrollTop;
  readerHistory.push({
    section, title, url,
    returnPage: current.id.slice(5),
    returnScroll: document.querySelector('main.content').scrollTop,
    scroll: 0, pdfIndex: 0,
  });
  renderReader();
}

async function renderReader() {
  const reading = readerHistory.at(-1);
  const version = ++readerVersion;
  showPage('reader');
  const content = $('reader-content');
  content.replaceChildren();
  $('reader-title').textContent = reading.title;
  $('reader-status').textContent = 'Идёт загрузка с СДО…';
  try {
    const api = window.pywebview.api;
    const result = reading.section === 'pdf'
      ? await api.pdf_page(reading.url, reading.pdfIndex)
      : await api.student_section(reading.section, '', reading.url);
    if (version !== readerVersion) return;
    if (!result.ok) throw Error(result.error);
    if (reading.section === 'pdf') renderPdf(result.data, reading);
    else paintReader(result.data, reading);
    if (reading.section === 'pdf') $('reader-status').textContent = '';
    document.querySelector('main.content').scrollTop = reading.scroll;
  } catch (error) {
    if (version === readerVersion) $('reader-status').textContent = error.message;
  }
}

function paintReader(data, reading) {
  const content = $('reader-content');
  content.replaceChildren();
  $('reader-status').textContent = data.notice || '';
  if (data.title) $('reader-title').textContent = data.title;
  if (data.url) reading.url = data.url;
  if (data.contact) {
    renderTeacher(data.contact, content);
  } else if (data.events) {
    const list = element('div', 'panel list');
    renderItems(list, data.events, 'event');
    content.append(list);
  } else {
    appendBlocks(content, data.blocks);
    for (const form of data.forms || []) content.append(renderActivityForm(form, reading));
    if (!data.blocks.length && !data.forms?.length) empty(content, 'СДО не вернула подробности.');
  }
}

function renderTeacher(contact, parent) {
  const form = element('form', 'panel resource-block activity-form');
  form.append(element('h2', '', contact.name));
  const open = internalLink('Открыть переписку', () => {
    showPage('messages');
    openConversation(contact);
  });
  form.append(open);
  if (!contact.canMessage) {
    form.append(element('p', 'muted', 'СДО ограничила отправку этому собеседнику.'));
  } else {
    const field = element('textarea');
    field.required = true;
    field.maxLength = 10000;
    field.setAttribute('aria-label', 'Вопрос преподавателю');
    const status = element('p', 'muted');
    const send = element('button', 'primary', 'Отправить вопрос');
    form.append(field, send, status);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      if (!field.value.trim()) return;
      send.disabled = true;
      try {
        const api = window.pywebview.api;
        const result = contact.id ? await api.send_message(contact.id, field.value) : await api.send_to_user(contact.userId, field.value);
        if (!result.ok) throw Error(result.error);
        field.value = '';
        status.textContent = 'СДО подтвердила отправку.';
      } catch (error) {
        status.textContent = error.message;
      } finally {
        send.disabled = false;
      }
    });
  }
  parent.append(form);
}

function makeActivityWidget(widget) {
  const label = element('label', 'activity-field');
  const input = element(['select', 'textarea'].includes(widget.kind) ? widget.kind : 'input');
  if (input.tagName === 'INPUT') input.type = widget.kind;
  input.name = widget.name;
  input.autocomplete = 'off';
  for (const option of widget.options) {
    const node = element('option', '', option.label);
    node.value = option.value;
    input.append(node);
  }
  input.value = widget.value;
  if (widget.kind === 'radio' || widget.kind === 'checkbox') {
    input.checked = widget.checked;
    label.classList.add('activity-choice');
    label.append(input, element('span', '', widget.label));
  } else {
    label.append(element('span', '', widget.label), input);
  }
  return label;
}

function renderActivityForm(data, reading) {
  const form = element('form', 'panel resource-block activity-form');
  const controls = element('fieldset');
  const status = element('p', 'muted');
  status.setAttribute('role', 'status');
  let files = [];
  if (data.upload) {
    controls.append(element('h2', '', 'Загрузить ответ'));
    if (data.existingCount) controls.append(element('p', 'muted', data.existingFiles.join(', ') || 'На СДО уже есть вложения.'));
    const pick = element('button', 'ghost', 'Прикрепить файл');
    pick.type = 'button';
    pick.disabled = data.maxFiles > 0 && data.existingCount >= data.maxFiles;
    const remove = element('button', 'ghost hidden', 'Отменить выбор');
    remove.type = 'button';
    const selection = element('p', 'muted');
    pick.onclick = async () => {
      try {
        const result = await window.pywebview.api.activity_pick(data.token);
        if (!result.ok) throw Error(result.error);
        if (result.names) {
          files = result.names;
          selection.textContent = files.join(', ');
          remove.classList.toggle('hidden', !files.length);
        }
      } catch (error) {
        status.textContent = error.message;
      }
    };
    remove.onclick = () => {
      files = [];
      selection.textContent = '';
      remove.classList.add('hidden');
    };
    controls.append(pick, remove, selection);
  }
  for (const group of data.groups) {
    if (group.content) controls.append(renderSourceNode(group.content));
    for (const widget of group.widgets) controls.append(makeActivityWidget(widget));
  }
  for (const [index, text] of data.buttons.entries()) {
    const button = element('button', 'primary', text);
    button.type = 'submit';
    button.value = String(index);
    controls.append(button);
  }
  form.append(controls, status);
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (data.upload && !files.length && !data.existingCount && !data.groups.some(group => group.widgets.length)) {
      status.textContent = 'Выберите файл для ответа.';
      return;
    }
    if (data.confirm && !window.confirm(event.submitter.textContent + '?')) return;
    const index = Number(event.submitter?.value || 0);
    const values = Object.fromEntries(new FormData(form));
    const version = readerVersion;
    controls.disabled = true;
    status.textContent = files.length ? 'Загружаю файл и сохраняю ответ…' : 'Отправляю данные в СДО…';
    try {
      const result = await window.pywebview.api.activity_submit(data.token, index, values, Boolean(files.length));
      if (version !== readerVersion) return;
      if (!result.ok) throw Error(result.error);
      paintReader(result.data, reading);
      $('reader-status').textContent = result.data.notice || 'СДО подтвердила действие.';
      document.querySelector('main.content').scrollTop = 0;
    } catch (error) {
      if (version !== readerVersion) return;
      status.textContent = error.message;
      const reload = element('button', 'ghost', 'Обновить форму');
      reload.type = 'button';
      reload.onclick = renderReader;
      form.append(reload);
    }
  });
  return form;
}

function renderLibrary(data) {
  const parent = $('resource-content');
  const filters = element('form', 'webinar-month');
  const input = element('input', 'library-search');
  input.type = 'search';
  input.value = libraryQuery;
  input.placeholder = 'Название вебинара';
  input.setAttribute('aria-label', 'Поиск по записям вебинаров');
  input.maxLength = 150;
  const button = element('button', 'ghost', 'Найти');
  filters.append(input, button, internalLink('Календарь вебинаров', () => showResource('webinars')));
  filters.onsubmit = event => {
    event.preventDefault();
    libraryQuery = input.value.trim();
    libraryPage = 0;
    showResource('library');
  };
  parent.append(filters);
  const cards = element('div', 'course-grid');
  for (const item of data.items) {
    const card = element('article', 'course-card library-card');
    if (item.image) {
      const img = element('img');
      img.src = item.image;
      img.alt = '';
      img.loading = 'lazy';
      card.append(img);
    }
    card.append(internalLink(item.title, () => openReader('recording', item.title, item.url)));
    card.append(element('small', '', [item.author, item.date].filter(Boolean).join(' · ')));
    cards.append(card);
  }
  if (!data.items.length) empty(cards, 'По этому запросу записей нет.');
  parent.append(cards);
  const paging = element('div', 'webinar-month');
  if (libraryPage) paging.append(internalLink('← Предыдущая страница', () => {
    --libraryPage;
    showResource('library');
  }));
  if (data.more) paging.append(internalLink('Следующая страница →', () => {
    ++libraryPage;
    showResource('library');
  }));
  parent.append(paging);
}

function renderPdf(result, reading) {
  const toolbar = element('div', 'pdf-toolbar');
  for (const [label, delta] of [['← Предыдущая', -1], ['Следующая →', 1]]) {
    const button = element('button', 'ghost', label);
    button.disabled = reading.pdfIndex + delta < 0 || reading.pdfIndex + delta >= result.total;
    button.onclick = () => {
      reading.pdfIndex += delta;
      reading.scroll = 0;
      renderReader();
    };
    toolbar.append(button);
  }
  toolbar.append(element('span', 'muted', (reading.pdfIndex + 1) + ' / ' + result.total));
  const image = element('img', 'pdf-page');
  image.alt = 'Страница ' + (reading.pdfIndex + 1);
  image.src = result.image;
  $('reader-content').append(toolbar, image);
}

function renderWebinars(data) {
  const parent = $('resource-content');
  const bar = element('div', 'webinar-month');
  bar.append(element('h2', '', data.month));
  bar.append(internalLink('Записи вебинаров', () => showResource('library')));
  for (const item of data.controls) {
    bar.append(internalLink(item.title, () => showResource('webinars', '', item.url)));
  }
  parent.append(bar);
  const list = element('div', 'panel list');
  parent.append(list);
  const items = data.events.map(event => {
    const day = event.date ? new Date(event.date * 1000).toLocaleDateString('ru-RU', {day: 'numeric', month: 'long', timeZone: 'Europe/Moscow'}) : '';
    return Object.assign({}, event, {when: [day, event.time].filter(Boolean).join(' · ')});
  });
  renderItems(list, items, 'event');
}

function renderProfile(data) {
  const panel = element('article', 'panel resource-block');
  const header = element('div', 'profile-heading');
  if (data.identity.avatar) {
    const photo = element('img', 'profile-photo');
    photo.src = data.identity.avatar;
    photo.alt = '';
    header.append(photo);
  }
  header.append(element('h2', '', data.identity.name || model.profile.name));
  panel.append(header);
  const list = element('dl', 'profile-fields');
  for (const field of data.fields) {
    list.append(element('dt', '', field.label), element('dd', '', field.value));
  }
  panel.append(list);
  if (data.programs.length) {
    const programs = element('ul');
    for (const program of data.programs) programs.append(element('li', '', program.label));
    panel.append(element('h3', '', 'Учебные программы'), programs);
  }
  $('resource-content').append(panel);
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
    if (kind === 'event' && item.id) {
      body.append(internalLink(item.title, () => openReader('event', item.title, String(item.id))));
    } else if (item.url && kind !== 'event') {
      body.append(internalLink(item.title, () => openMaterial(item.url, item.title)));
    } else {
      body.append(element('strong', '', item.title));
    }
    const detail = item.when ? String(item.when) : item.description || '';
    if (detail) body.append(element('small', '', detail));
    if (item.completion) body.append(element('small', 'completion-note', item.completion));
    row.append(body);
    parent.append(row);
  }
}
function render() {
  $('count-notifications').textContent = model.notifications.length;
  $('count-notifications').classList.toggle('hidden', !model.notifications.length);
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

async function renderHomePrograms() {
  const version = accountVersion;
  const list = $('home-programs');
  empty(list, 'Загружаю программы…');
  try {
    const programs = await getPrograms();
    if (version !== accountVersion) return;
    list.replaceChildren();
    for (const program of programs) {
      const item = element('details', 'program-item');
      item.append(element('summary', '', program.label));
      const links = element('div', 'program-links');
      links.append(
        internalLink('Учебный план', () => showPage('curriculum', program.id)),
        internalLink('Зачётная книжка', () => showResource('grades', '', '', program.id)),
      );
      item.append(links);
      list.append(item);
    }
  } catch (error) {
    if (version === accountVersion) empty(list, error.message);
  }
}

async function selectCourse(course) {
  const version = ++topicVersion;
  logAction('course', course.id);
  $('assignment-heading').textContent = course.title;
  document.querySelectorAll('#assignment-courses button').forEach(node => node.classList.toggle('active', node.dataset.courseId == course.id));
  empty($('assignment-list'), 'Загружаю темы…');
  const result = await window.pywebview.api.topics(course.url);
  if (version !== topicVersion) return;
  if (!result.ok) { empty($('assignment-list'), result.error); return; }
  if (!result.data.items.length) { empty($('assignment-list'), 'СДО не вернула темы курса.'); return; }
  const topics = result.data.items.map(item => Object.assign({}, item, {description: materialLabels[item.kind] || ''}));
  renderItems($('assignment-list'), topics, 'deadline');
  if (result.data.overview.length) {
    const overview = element('section', 'course-overview');
    overview.append(element('h3', '', 'О дисциплине'));
    for (const block of result.data.overview) overview.append(renderSourceNode(block));
    $('assignment-list').prepend(overview);
  }
}
let curriculumLoaded = '';
let curriculumVersion = 0;
async function loadCurriculum(selectedProgram = '') {
  const selector = $('curriculum-program');
  if (curriculumLoaded && curriculumLoaded === (selectedProgram || selector.value)) return;
  curriculumLoaded = '';
  const version = ++curriculumVersion;
  const list = $('curriculum-list');
  empty(list, 'Загружаю учебный план…');
  try {
    await fillPrograms(selector);
  } catch (error) {
    if (version === curriculumVersion) empty(list, error.message);
    return;
  }
  if (version !== curriculumVersion) return;
  if (selectedProgram) selector.value = selectedProgram;
  const program = selector.value;
  const result = await window.pywebview.api.curriculum(program);
  if (version !== curriculumVersion || program !== selector.value) return;
  if (!result.ok) { empty($('curriculum-list'), result.error); return; }
  curriculumLoaded = program;
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
  ++accountVersion;
  model = result.data;
  const name = result.data.profile?.name || result.username;
  const [surname, personal, patronymic] = name.trim().split(/\s+/);
  const initials = [surname, patronymic].filter(Boolean).map(word => Array.from(word)[0].toUpperCase() + '.');
  $('account-name').textContent = personal ? [personal, initials.join('')].join(' ') : name;
  $('account-name').title = name;
  $('top-account-name').textContent = name;
  $('account-avatar').textContent = (personal || name).charAt(0).toUpperCase();
  $('login').classList.add('hidden'); $('app').classList.remove('hidden');
  $('password').value = '';
  render();
  renderHomePrograms();
  loadAccountAvatar(accountVersion);
  const lastPage = sessionStorage.getItem('osek-page');
  if (lastPage && document.getElementById('page-' + lastPage)) showPage(lastPage);
}

async function loadAccountAvatar(version) {
  const result = await window.pywebview.api.student_section('profile');
  if (version !== accountVersion || !result.ok || !result.data.identity.avatar) return;
  const image = element('img');
  image.src = result.data.identity.avatar;
  image.alt = '';
  $('account-avatar').replaceChildren(image);
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
  loadCurriculum();
});
$('reader-back').addEventListener('click', () => {
  const reading = readerHistory.pop();
  if (readerHistory.length) {
    renderReader();
  } else {
    showPage(reading.returnPage);
    if (reading.returnPage === 'resources') {
      document.querySelector(`[data-section="${activeResource}"]`)?.classList.add('active');
    }
    document.querySelector('main.content').scrollTop = reading.returnScroll;
  }
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
  ++curriculumVersion;
  ++accountVersion;
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
  curriculumLoaded = '';
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
