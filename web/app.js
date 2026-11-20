const $ = (id) => document.getElementById(id);
const serverLoading = 'Загружаю информацию с сервера...';
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
const activityModules = new Set(['quiz', 'assign', 'assignauto', 'assignpractice']);
const isCourseActivity = item => activityModules.has(item.kind) || /^(?:практическ(?:ое занятие|ая работа|ое задание)|тест|(?:промежуточная |итоговая )?аттестация|(?:демонстрационный )?экзамен)(?:\s|:|$)/i.test(item.title);
let courseReference = null;
let referenceVersion = 0;
const imageObserver = new IntersectionObserver(entries => {
  for (const entry of entries) {
    if (!entry.isIntersecting) continue;
    const image = entry.target;
    imageObserver.unobserve(image);
    loadCourseImage(image);
  }
}, {rootMargin: '250px'});

function teacherPhoto(url, className = 'teacher-avatar') {
  const image = element('img', className);
  image.alt = '';
  image.dataset.sdoImage = url;
  imageObserver.observe(image);
  return image;
}
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
let progressVersion = 0;
let planVersion = 0;
let notificationReads = Promise.resolve();
let courseReturnPage = "courses";
const progressCache = new Map();
const dayCache = new Map();
let homeCacheOwner = '';
let cachedPrograms = [];
let cachedPanels = {};
let cacheTimer;
function scheduleHomeCache() {
  if (!homeCacheOwner) return;
  clearTimeout(cacheTimer);
  cacheTimer = setTimeout(() => {
    window.pywebview.api.save_home_cache?.(homeCacheOwner, {
      programs: cachedPrograms, plans: [...homePlanCache].slice(-20),
      progress: [...progressCache].slice(-100), days: [...dayCache].slice(-28), panels: cachedPanels,
    }).catch(() => {});
  }, 250);
}

const homePlanCache = new Map();
const sectionCache = new Map();
const pendingProgress = new Set();
let progressLoading = false;
let dayVersion = 0;
let selectedDay = new Date();
let visibleMonth = new Date(selectedDay.getFullYear(), selectedDay.getMonth(), 1);
let selectedDayEvents = null;
const monthFormat = new Intl.DateTimeFormat('ru-RU', {month: 'long', year: 'numeric'});
const dayFormat = new Intl.DateTimeFormat('ru-RU', {dateStyle: 'full'});
const navGroups = [...document.querySelectorAll('.nav-group')];
const logAction = (kind, detail = '') => { const task = window.pywebview?.api?.log_event(kind, String(detail)); if (task?.catch) task.catch(() => {}); };

function animateIn(node) {
  if (!node || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  for (const animation of node.getAnimations()) {
    if (animation.id === 'osek-enter') animation.cancel();
  }
  node.animate([
    {opacity: 0, transform: 'translateY(7px)'},
    {opacity: 1, transform: 'translateY(0)'},
  ], {id: 'osek-enter', duration: 170, easing: 'ease-out'});
}

function renderNotificationCount() {
  const count = model.unread_notifications || 0;
  const badge = $('count-notifications');
  badge.textContent = count;
  badge.classList.toggle('hidden', !count);
  const bell = badge.closest('button');
  bell.setAttribute('aria-label', count ? `Уведомления СДО, непрочитанных: ${count}` : 'Уведомления СДО');
}

function markNotification(item) {
  if (!item.id || item.read || item.marking) return;
  const account = accountVersion;
  item.marking = true;
  notificationReads = notificationReads.then(async () => {
    if (account !== accountVersion) return;
    try {
      const result = await window.pywebview.api.mark_notification_read(item.id);
      if (account !== accountVersion) return;
      if (!result.ok) throw Error(result.error);
      const current = model.notifications.find(notification => notification.id === item.id);
      if (result.data.unread !== null) model.unread_notifications = result.data.unread;
      else if (current && !current.read) model.unread_notifications = Math.max(0, (model.unread_notifications || 0) - 1);
      item.read = true;
      if (current) current.read = true;
      renderNotificationCount();
    } catch (error) {
      if (account === accountVersion) showMessage(error.message || 'Не удалось отметить уведомление прочитанным');
    } finally {
      item.marking = false;
    }
  });
}

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

function paintPrograms(selector, programs) {
  for (const program of programs) {
    const option = element('option', '', program.label || 'Программа ' + program.id);
    option.value = program.id;
    option.selected = program.active;
    selector.append(option);
  }
}

async function fillPrograms(selector) {
  if (selector.options.length) return;
  const version = accountVersion;
  const programs = await getPrograms();
  if (version !== accountVersion || selector.options.length) return;
  paintPrograms(selector, programs);
}

function showPage(page, program = '') {
  closeNavigation();
  logAction('tab', page);
  if (!['resources', 'reader', 'course'].includes(page)) sessionStorage.setItem('osek-page', page);
  if (page !== 'reader') {
    readerHistory.length = 0;
    ++readerVersion;
    $('reader-content').replaceChildren();
    syncCourseReference(null);
  }
  document.querySelectorAll('.page').forEach(node => node.classList.toggle('hidden', node.id !== 'page-' + page));
  document.querySelectorAll('.nav').forEach(node => node.classList.toggle('active', node.dataset.page === page || page === 'course' && node.dataset.page === 'courses'));
  document.querySelector('main.content').scrollTop = 0;
  animateIn($('page-' + page));
  if (page === 'curriculum') loadCurriculum(program || $('nav-program').value);
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

function renderSourceNode(value, follow = openMaterial) {
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
      follow(value.attrs.href, node.textContent.trim());
    });
  }
  node.append(...value.children.map(child => renderSourceNode(child, follow)));
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
  const content = $('resource-content');
  $('resource-title').textContent = title || sectionTitles[section];
  const paint = data => {
    content.replaceChildren();
    if (data.title && !title) $('resource-title').textContent = data.title;
    if (section === 'profile') renderProfile(data);
    else if (section === 'webinars') renderWebinars(data);
    else if (section === 'library') renderLibrary(data);
    else appendBlocks(content, data.blocks);
  };
  let cached;
  content.replaceChildren();
  $('resource-status').textContent = serverLoading;
  const needsProgram = programSections.has(section);
  const selector = $('resource-program');
  $('resource-program-label').classList.toggle('hidden', !needsProgram);
  try {
    if (needsProgram && !selector.options.length) {
      await fillPrograms(selector);
      if (version !== resourceVersion) return;
    }
    if (needsProgram && program) selector.value = program;
    const key = section !== 'material' ? JSON.stringify([section, needsProgram ? selector.value : '', url,
      section === 'library' ? [libraryPage, libraryQuery] : null]) : null;
    cached = key && sectionCache.get(key);
    if (cached) {
      paint(cached);
      $('resource-status').textContent = 'Обновляю данные…';
    }
    const api = window.pywebview.api;
    const result = section === 'library'
      ? await api.recordings(libraryPage, libraryQuery)
      : await api.student_section(section, needsProgram ? selector.value : '', url);
    if (version !== resourceVersion) return;
    if (!result.ok) throw Error(result.error);
    $('resource-status').textContent = '';
    if (key) {
      sectionCache.set(key, result.data);
      if (sectionCache.size > 24) sectionCache.delete(sectionCache.keys().next().value);
    }
    if (!cached || JSON.stringify(cached) !== JSON.stringify(result.data)) paint(result.data);
  } catch (error) {
    if (version === resourceVersion) $('resource-status').textContent =
      cached ? 'Не удалось обновить. Показаны последние загруженные данные.' : error.message;
  }
}

function appendBlocks(target, blocks, follow = openMaterial) {
  for (const block of blocks) {
    const panel = element('article', 'panel resource-block');
    if (block.tag === 'img') panel.classList.add('reader-book-page');
    panel.append(renderSourceNode(block, follow));
    target.append(panel);
  }
}

function materialSection(url) {
  const address = new URL(url);
  if (address.pathname === '/calendar/view.php' && address.searchParams.get('view') === 'day') return 'calendar_day';
  if (address.hostname === 'sdo.vgaps.ru' && address.pathname === '/library.php') return 'recording';
  return address.pathname.toLowerCase().endsWith('.pdf') || address.pathname === '/report/studybook/getpdf.php'
    || (address.pathname === '/mod/gbooks/view.php' && address.searchParams.has('pdf')) ? 'pdf' : 'material';
}

function openMaterial(url, title, reference = null) {
  openReader(materialSection(url), title, url, '', reference);
}

function syncCourseReference(reference) {
  if (courseReference === reference) return;
  ++referenceVersion;
  courseReference = reference;
  $('reader-layout').classList.toggle('reader-split', Boolean(reference));
  const panel = $('course-reference');
  panel.classList.toggle('hidden', !reference);
  if (reference) {
    $('course-reference-name').textContent = reference.course.title;
    renderReferenceTopics();
  } else {
    $('course-reference-content').replaceChildren();
  }
}

function renderReferenceTopics() {
  if (!courseReference) return;
  ++referenceVersion;
  $('course-reference-back').classList.add('hidden');
  const content = $('course-reference-content');
  content.replaceChildren();
  let heading = '';
  for (const item of courseReference.topics) {
    if (item.kind === 'раздел') {
      heading = item.title;
      continue;
    }
    if (!item.url || isCourseActivity(item) || ['forum', 'askquest'].includes(item.kind)) continue;
    if (heading) {
      content.append(element('h3', 'topic-section-title', heading));
      heading = '';
    }
    const link = internalLink(item.title, () => openReferenceMaterial(item.url, item.title));
    content.append(link);
  }
  if (!content.firstChild) empty(content, 'СДО не вернула материалы этого предмета.');
  $('course-reference').scrollTop = 0;
}

async function openReferenceMaterial(url, title, pdfIndex = 0) {
  if (!courseReference) return;
  const version = ++referenceVersion;
  const content = $('course-reference-content');
  $('course-reference-back').classList.remove('hidden');
  empty(content, serverLoading);
  try {
    const section = materialSection(url);
    if ([...activityModules, 'forum', 'askquest'].includes(new URL(url).pathname.split('/')[2]) || !['material', 'pdf'].includes(section)) {
      throw Error('Откройте учебный материал этого предмета из списка тем.');
    }
    const api = window.pywebview.api;
    const result = section === 'pdf' ? await api.pdf_page(url, pdfIndex) : await api.student_section('material', '', url);
    if (version !== referenceVersion) return;
    if (!result.ok) throw Error(result.error);
    content.replaceChildren(element('h3', '', result.data.title || title || 'Учебный материал'));
    if (section === 'pdf') {
      appendPdf(content, result.data, pdfIndex, index => openReferenceMaterial(url, title, index));
    } else {
      appendBlocks(content, result.data.blocks || [], openReferenceMaterial);
      if (!result.data.blocks?.length) content.append(element('p', 'muted', 'СДО не вернула содержимое темы.'));
    }
    $('course-reference').scrollTop = 0;
  } catch (error) {
    if (version === referenceVersion) empty(content, error.message);
  }
}

$('course-reference-back').addEventListener('click', renderReferenceTopics);

function openReader(section, title, url, avatar = '', reference = null) {
  const current = document.querySelector('.page:not(.hidden)');
  const previous = readerHistory.at(-1);
  if (previous) previous.scroll = document.querySelector('main.content').scrollTop;
  readerHistory.push({
    section, title, url, avatar, reference: reference || previous?.reference || null,
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
  syncCourseReference(reading.reference);
  const content = $('reader-content');
  content.replaceChildren();
  $('reader-title').textContent = reading.title;
  $('reader-status').textContent = serverLoading;
  try {
    const api = window.pywebview.api;
    const result = reading.section === 'pdf'
      ? await api.pdf_page(reading.url, reading.pdfIndex)
      : await api.student_section(reading.section, '', reading.url, reading.section === 'material');
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
    renderTeacher(data.contact, content, reading.avatar);
  } else if (data.events) {
    const list = element('div', 'panel list');
    renderItems(list, data.events, 'event');
    content.append(list);
  } else {
    appendBlocks(content, data.blocks);
    if (typeof data.subscription === 'boolean') {
      const panel = element('div', 'panel resource-block');
      const button = element('button', 'primary', data.subscription ? 'Отписаться' : 'Подписаться');
      const status = element('p', 'muted');
      status.setAttribute('role', 'status');
      let subscribed = data.subscription;
      button.addEventListener('click', async () => {
        button.disabled = true;
        try {
          const result = await window.pywebview.api.set_meeting_subscription(reading.url, !subscribed);
          if (!result.ok) throw Error(result.error);
          subscribed = result.data.subscribed;
          button.textContent = subscribed ? 'Отписаться' : 'Подписаться';
          status.textContent = subscribed ? 'Подписка подтверждена СДО.' : 'Подписка отменена.';
        } catch (error) {
          status.textContent = error.message;
        } finally {
          button.disabled = false;
        }
      });
      panel.append(button, status);
      content.append(panel);
    }
    for (const form of data.forms || []) content.append(renderActivityForm(form, reading));
    if (!data.blocks.length && !data.forms?.length) empty(content, 'СДО не вернула подробности.');
  }
}

function renderTeacher(contact, parent, fallbackAvatar) {
  const form = element('form', 'panel resource-block activity-form');
  const avatarUrl = contact.avatar || fallbackAvatar;
  if (avatarUrl) form.append(teacherPhoto(avatarUrl));
  form.append(element('h2', '', contact.name));
  const open = internalLink(contact.canMessage ? 'Написать сообщение' : 'Открыть переписку', () => {
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

function curriculumTeacher(item) {
  if (!item.teacher_url) return element('span', '', item.teacher);
  return internalLink(item.teacher, () => openReader('teacher', item.teacher, item.teacher_url, item.teacher_avatar));
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

function appendPdf(target, result, index, turnPage) {
  const toolbar = element('div', 'pdf-toolbar');
  for (const [label, delta] of [['← Предыдущая', -1], ['Следующая →', 1]]) {
    const button = element('button', 'ghost', label);
    button.disabled = index + delta < 0 || index + delta >= result.total;
    button.onclick = () => turnPage(index + delta);
    toolbar.append(button);
  }
  toolbar.append(element('span', 'muted', (index + 1) + ' / ' + result.total));
  const image = element('img', 'pdf-page');
  image.alt = 'Страница ' + (index + 1);
  image.src = result.image;
  target.append(toolbar, image);
}

function renderPdf(result, reading) {
  appendPdf($('reader-content'), result, reading.pdfIndex, index => {
    reading.pdfIndex = index;
    reading.scroll = 0;
    renderReader();
  });
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
function renderItems(parent, items, kind, follow = (url, title) => openMaterial(url, title)) {
  parent.replaceChildren();
  if (!items.length) { empty(parent, kind === 'deadline' ? 'Сроки пока недоступны. Проверьте задания в разделе «Курсы».' : 'Пока ничего нет.'); return; }
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
        if (!details.open) return;
        animateIn(details.querySelector('.notification-text'));
        logAction('notification', 'open');
        markNotification(item);
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
      body.append(internalLink(item.title, () => follow(item.url, item.title, item)));
    } else {
      body.append(element('strong', '', item.title));
    }
    const detail = item.when ? String(item.when) : item.description || '';
    const lessonType = kind === 'event' ? calendarLessonLabel(item.title) : '';
    if (lessonType.startsWith('Демонстрационный экзамен')) body.append(element('small', 'event-lesson-type', lessonType));
    if (detail) body.append(element('small', '', detail));
    if (item.completion) body.append(element('small', 'completion-note', item.completion));
    row.append(body);
    parent.append(row);
  }
}
function render() {
  renderNotificationCount();
  renderMiniCalendar();
  renderProgress();
  renderItems($('home-deadlines'), model.deadlines, 'deadline');
  renderItems($('notifications-list'), model.notifications, 'notification');
  const cards = $('courses-list'); cards.replaceChildren();
  for (const course of model.courses) {
    const card = element('button', 'course-card');
    card.append(element('div', 'course-mark', '▤'), element('strong', '', course.title), element('small', '', 'Смотреть темы →'));
    card.addEventListener('click', () => openCourse(course));
    cards.append(card);
  }
  if (!model.courses.length) empty(cards, 'СДО не вернула список курсов.');
}

function closeNavigation(except = null) {
  for (const group of navGroups) {
    if (group !== except) group.open = false;
  }
}

function showProgramName() {
  const choice = $('nav-program').selectedOptions[0];
  $('home-program-name').textContent = choice?.textContent || '';
  $('home-program-name').classList.toggle('hidden', !choice);
}

function selectHomeProgram(id) {
  const selector = $('nav-program');
  if (id && selector.value !== id && [...selector.options].some(option => option.value === id)) {
    selector.value = id;
    showProgramName();
    loadHomePlan();
  }
}

async function renderHomePrograms() {
  const version = accountVersion;
  const selector = $('nav-program');
  const show = () => {
    $('nav-program-label').classList.remove('hidden');
    $('home-program-status').textContent = '';
    showProgramName();
  };
  if (cachedPrograms.length) {
    paintPrograms(selector, cachedPrograms);
    show();
    loadHomePlan();
  } else {
    $('home-program-status').textContent = serverLoading;
  }
  try {
    const programs = await getPrograms();
    if (version !== accountVersion) return;
    const previous = selector.value;
    const hadCached = cachedPrograms.length > 0;
    if (JSON.stringify(programs) !== JSON.stringify(cachedPrograms)) {
      selector.replaceChildren();
      paintPrograms(selector, programs);
      if ([...selector.options].some(option => option.value === previous)) selector.value = previous;
    }
    cachedPrograms = programs;
    show();
    scheduleHomeCache();
    if (!hadCached || selector.value !== previous) loadHomePlan();
  } catch (error) {
    if (version === accountVersion && !cachedPrograms.length) $('home-program-status').textContent = error.message;
  }
}

function paintHomePlan(plan, items) {
  plan.replaceChildren();
  for (const item of items) {
    const row = element('div', 'home-plan-row');
    row.append(internalLink(item.title, () => openCurriculumCourse(item)));
    const details = [item.hours, item.control].filter(Boolean);
    if (item.teacher || details.length) {
      const line = element('small');
      if (item.teacher) line.append(curriculumTeacher(item));
      if (details.length) line.append((item.teacher ? ' · ' : '') + details.join(' · '));
      row.append(line);
    }
    plan.append(row);
  }
  if (!items.length) empty(plan, 'СДО не показала дисциплины для этой программы.');
}

async function loadHomePlan() {
  const version = ++planVersion;
  const program = $('nav-program').value;
  const plan = $('home-plan');
  const cached = homePlanCache.get(program);
  if (cached) paintHomePlan(plan, cached);
  else empty(plan, serverLoading);
  try {
    const result = await window.pywebview.api.curriculum(program);
    if (version !== planVersion) return;
    if (!result.ok) throw Error(result.error);
    if (!cached || JSON.stringify(cached) !== JSON.stringify(result.items)) paintHomePlan(plan, result.items);
    homePlanCache.set(program, result.items);
    scheduleHomeCache();
  } catch (error) {
    if (version !== planVersion) return;
    if (!cached) empty(plan, error.message);
    else plan.append(element('p', 'muted', 'Не удалось обновить. Показан последний загруженный план.'));
  }
}

async function drainProgress() {
  if (progressLoading) return;
  progressLoading = true;
  try {
    while (pendingProgress.size) {
      const row = pendingProgress.values().next().value;
      pendingProgress.delete(row);
      if (row.isConnected && row.visibleProgress && !row.progressStarted) await row.loadProgress();
    }
  } finally {
    progressLoading = false;
  }
}

const progressObserver = new IntersectionObserver(entries => {
  for (const entry of entries) {
    const row = entry.target;
    row.visibleProgress = entry.isIntersecting;
    if (entry.isIntersecting && row.isConnected && !row.progressStarted) pendingProgress.add(row);
    else pendingProgress.delete(row);
  }
  drainProgress();
}, {root: $('home-progress')});

function renderProgress() {
  const version = ++progressVersion;
  progressObserver.disconnect();
  pendingProgress.clear();
  const panel = $('home-progress');
  panel.replaceChildren();
  if (!model.courses.length) {
    empty(panel, 'Курсы пока недоступны.');
    return;
  }
  for (const course of model.courses) {
    const row = element('div', 'progress-row');
    const heading = element('div', 'progress-heading');
    const status = element('small', 'progress-value');
    heading.append(internalLink(course.title, () => openCourse(course)), status);
    row.append(heading);
    panel.append(row);
    const paint = ({completed, total}) => {
      row.querySelector('progress')?.remove();
      if (!total) {
        status.textContent = 'Нет отметок СДО';
        status.removeAttribute('title');
        return;
      }
      status.textContent = Math.round(100 * completed / total) + '%';
      status.title = `Выполнено тем с отметками СДО: ${completed} из ${total}`;
      const bar = element('progress', 'study-progress');
      bar.max = total;
      bar.value = completed;
      bar.setAttribute('aria-label', course.title + ' · выполнено тем с отметками СДО');
      row.append(bar);
    };
    const cached = progressCache.get(course.url);
    if (cached) paint(cached.data);
    row.loadProgress = async () => {
      row.progressStarted = true;
      progressObserver.unobserve(row);
      if (cached && Date.now() - cached.time < 90_000) return;
      if (!cached) status.textContent = 'Загружаю…';
      try {
        const result = await window.pywebview.api.course_progress(course.url);
        if (version !== progressVersion) return;
        if (!result.ok) throw Error(result.error);
        progressCache.set(course.url, {data: result.data, time: Date.now()});
        scheduleHomeCache();
        paint(result.data);
      } catch {
        if (version !== progressVersion) return;
        if (!cached) status.textContent = 'Прогресс недоступен';
        else status.title = 'Не удалось обновить. Показан последний загруженный прогресс.';
      }
    };
    progressObserver.observe(row);
  }
}

function dayKey(date) {
  return [date.getFullYear(), date.getMonth(), date.getDate()].join('/');
}

function eventDay(item) {
  if (!item.url) return '';
  const time = Number(new URL(item.url).searchParams.get('time'));
  return Number.isFinite(time) && time > 0 ? dayKey(new Date(time * 1000)) : '';
}

function calendarLessonLabel(name) {
  const labels = {лк: 'Лекция', пз: 'Практическое занятие', семинар: 'Семинар', дэ: 'Демонстрационный экзамен'};
  const suffix = name.match(/\s+\((лк|пз|семинар|дэ)\s*(\d*)\)\s*$/i)
    || name.match(/(?:^|\s[–—-]\s|\s+)(дэ)\s*(\d*)\s*$/i);
  if (!suffix) return '';
  const label = labels[suffix[1].toLowerCase()];
  return suffix[2] ? label + ' ' + suffix[2] : label;
}

function renderMiniEvents() {
  const events = selectedDayEvents ?? model.events.filter(item => eventDay(item) === dayKey(selectedDay));
  const list = $('mini-events');
  list.replaceChildren();
  if (!events.length) {
    empty(list, 'На этот день в СДО нет событий.');
    return;
  }
  for (const item of events) {
    const row = element('div', 'item');
    const body = element('div', 'item-body');
    const times = String(item.when || '').match(/\b(?:[01]?\d|2[0-3]):[0-5]\d\b/g);
    const time = times?.slice(0, 2).join(' – ') || item.when;
    if (time) body.append(element('small', 'event-time', time));

    const title = item.title;
    const label = calendarLessonLabel(title);
    if (item.id) body.append(internalLink(title, () => openReader('event', item.title, String(item.id))));
    else body.append(element('strong', '', title));
    if (label) body.append(element('small', 'event-lesson-type', label));
    row.append(body);
    list.append(row);
  }
}

function renderMiniCalendar() {
  const month = visibleMonth.getMonth();
  const year = visibleMonth.getFullYear();
  const slot = $('mini-days');
  const eventDays = new Set(model.events.map(eventDay));
  const selected = dayKey(selectedDay);
  const today = dayKey(new Date());
  $('month-label').textContent = monthFormat.format(visibleMonth);
  slot.replaceChildren();
  const offset = (visibleMonth.getDay() + 6) % 7;
  for (let index = 0; index < offset; ++index) slot.append(element('span'));
  const lastDay = new Date(year, month + 1, 0).getDate();
  for (let day = 1; day <= lastDay; ++day) {
    const date = new Date(year, month, day, 12);
    const key = dayKey(date);
    const button = element('button', 'calendar-day', day);
    button.type = 'button';
    button.setAttribute('aria-label', dayFormat.format(date));
    button.setAttribute('aria-pressed', String(key === selected));
    if (key === today) button.setAttribute('aria-current', 'date');
    button.classList.toggle('has-events', eventDays.has(key));
    button.addEventListener('click', () => loadMiniDay(date, true));
    slot.append(button);
  }
  renderMiniEvents();
}

async function loadMiniDay(date, focus = false) {
  const version = ++dayVersion;
  selectedDay = date;
  const key = dayKey(date);
  const cached = dayCache.get(key);
  selectedDayEvents = cached ?? null;
  renderMiniCalendar();
  if (focus) $('mini-days').querySelector('[aria-pressed=true]')?.focus();
  if (!cached) empty($('mini-events'), serverLoading);
  try {
    const time = Math.round(date.getTime() / 1000);
    const url = 'https://sdo.i-spo.ru/calendar/view.php?view=day&time=' + time;
    const result = await window.pywebview.api.student_section('calendar_day', '', url);
    if (version !== dayVersion) return;
    if (!result.ok) throw Error(result.error);
    selectedDayEvents = result.data.events || [];
    dayCache.delete(key);
    dayCache.set(key, selectedDayEvents);
    scheduleHomeCache();
    renderMiniEvents();
  } catch (error) {
    if (version === dayVersion && !cached) empty($('mini-events'), error.message);
  }
}

function openCurriculumCourse(item) {
  const url = new URL(item.url);
  openCourse({id: url.searchParams.get('id'), title: item.title, url: url.href});
}

function openCourse(course) {
  const current = document.querySelector('.page:not(.hidden)');
  if (current && current.id !== 'page-course') courseReturnPage = current.id.slice(5);
  showPage('course');
  selectCourse(course);
}

async function selectCourse(course) {
  const version = ++topicVersion;
  logAction('course', course.id);
  $('course-heading').textContent = course.title;
  empty($('assignment-list'), 'Загружаю темы…');
  const result = await window.pywebview.api.topics(course.url);
  if (version !== topicVersion) return;
  if (!result.ok) { empty($('assignment-list'), result.error); return; }
  if (!result.data.items.length) { empty($('assignment-list'), 'СДО не вернула темы курса.'); return; }
  const topics = result.data.items.map(item => Object.assign({}, item, {description: materialLabels[item.kind] || ''}));
  const reference = {course, topics};
  renderItems($('assignment-list'), topics, 'deadline', (url, title, item) => {
    openMaterial(url, title, isCourseActivity(item) ? reference : null);
  });
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
    const card = element('article', 'course-card curriculum-card');
    const mark = item.teacher_avatar
      ? teacherPhoto(item.teacher_avatar, 'course-mark teacher-avatar')
      : element('div', 'course-mark', '◈');
    card.append(mark, internalLink(item.title, () => openCurriculumCourse(item)));
    if (item.teacher) {
      const teacher = element('p', '', 'Преподаватель: ');
      teacher.append(curriculumTeacher(item));
      card.append(teacher);
    }
    if (item.hours) card.append(element('small', '', 'Часы: ' + item.hours));
    if (item.control) card.append(element('small', '', 'Форма контроля: ' + item.control));
    if (item.grade) card.append(element('small', '', item.grade));
    list.append(card);
  }
  if (!result.items.length) empty(list, 'Учебный план пока недоступен.');
}
async function signedIn(result) {
  const account = ++accountVersion;
  const cache = await window.pywebview.api.load_home_cache?.().catch(() => ({})) || {};
  if (account !== accountVersion) return;
  homeCacheOwner = result.username;
  cachedPrograms = cache.programs || [];
  cachedPanels = cache.panels || {};
  for (const [map, entries] of [[homePlanCache, cache.plans], [dayCache, cache.days], [progressCache, cache.progress]]) {
    map.clear();
    for (const [key, value] of entries || []) map.set(key, value);
  }
  model = {...result.data, ...cachedPanels.calendar};
  const name = result.data.profile?.name || result.username;
  const [surname, personal, patronymic] = name.trim().split(/\s+/);
  const initials = [personal, patronymic].filter(Boolean).map(word => Array.from(word)[0].toUpperCase() + '.');
  $('account-name').textContent = initials.length ? [surname, initials.join('')].join(' ') : name;
  $('account-name').title = name;
  $('account-avatar').textContent = (personal || name).charAt(0).toUpperCase();
  $('client-caption').classList.remove('hidden');
  $('login').classList.add('hidden'); $('app').classList.remove('hidden');
  $('password').value = '';
  renderHomePrograms();
  render();
  loadMiniDay(selectedDay);
  loadDashboardPanels(accountVersion);
  const avatar = model.profile?.avatar;
  if (avatar) $('account-avatar').replaceChildren(teacherPhoto(avatar));
  else loadAccountAvatar(accountVersion);
  const lastPage = sessionStorage.getItem('osek-page');
  if (lastPage && document.getElementById('page-' + lastPage)) showPage(lastPage);
  else showPage('home');
}

async function loadDashboardPanels(account) {
  const api = window.pywebview.api;
  if (!api.dashboard_panel) return;
  empty($('home-deadlines'), serverLoading);
  empty($('notifications-list'), serverLoading);
  for (const panel of ['calendar', 'deadlines', 'notifications']) {
    if (account !== accountVersion) break;
    try {
      const result = await api.dashboard_panel(panel);
      if (account !== accountVersion) break;
      if (!result.ok) throw Error(result.error);
      Object.assign(model, result.data);
      if (panel === 'calendar') { cachedPanels[panel] = result.data; scheduleHomeCache(); }
      if (panel === 'calendar') renderMiniCalendar();
      if (panel === 'deadlines') renderItems($('home-deadlines'), model.deadlines, 'deadline');
      if (panel === 'notifications') {
        renderNotificationCount();
        renderItems($('notifications-list'), model.notifications, 'notification');
      }
    } catch (error) {
      if (account !== accountVersion) break;
      if (panel === 'deadlines') empty($('home-deadlines'), error.message);
      if (panel === 'notifications') empty($('notifications-list'), error.message);
    }
  }
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
  showReleaseStatus().catch(() => logAction('ui_error', 'release_status_failed'));
  const settings = await window.pywebview.api.settings();
  applyAppearance(settings.theme);
  savedAppearance = document.body.dataset.theme;
  const result = await window.pywebview.api.bootstrap();
  $('username').value = result.username || '';
  if (result.saved) {
    $('resume-state').classList.remove('hidden'); $('login-form').classList.add('hidden');
    const resumed = await window.pywebview.api.resume();
    $('resume-state').classList.add('hidden'); $('login-form').classList.remove('hidden');
    if (resumed.ok) await signedIn(resumed);
    else $('login-status').textContent = resumed.error || 'Не удалось войти автоматически';
  }
  $('autostart').checked = !!settings.autostart;
  $('notification-sounds').checked = settings.sounds !== false;
  $('log-path').textContent = await window.pywebview.api.get_log_path();
}

const updateDialog = $('update-dialog');
let updateBusy = false;
let updateResult = null;
let promptedUpdate = null;
let releasePaintGeneration = 0;

function paintUpdateProgress(progress) {
  for (const id of ['settings-update-progress', 'dialog-update-progress']) {
    const bar = $(id);
    bar.classList.toggle('hidden', !progress);
    if (progress) bar.value = progress.phase === 'restart' ? 100 : Math.floor(100 * progress.received / progress.total);
  }
}

function paintRelease(result) {
  updateResult = result;
  const version = result.version ? `v0.${result.version}` : '';
  const label = result.name || version;
  $('settings-update-status').textContent = result.failure || (version
    ? `Доступно обновление ${label}${label.includes(version) ? '' : ' · ' + version}`
    : result.ok ? 'Установлена актуальная версия.' : 'Проверяю наличие обновлений…');
  $('settings-install-update').classList.toggle('hidden', !result.automatic);
  $('dialog-install-update').classList.toggle('hidden', !result.automatic);
  const fallback = $('settings-manual-update');
  fallback.classList.toggle('hidden', !result.url || result.automatic);
  if (result.url) fallback.href = result.url;
  else fallback.removeAttribute('href');
  if (!version && updateDialog.open) updateDialog.close();
  if (version && result.prompts && promptedUpdate !== version && document.visibilityState === 'visible') {
    promptedUpdate = version;
    $('update-release-name').textContent = label;
    $('update-dialog-status').textContent = result.automatic
      ? 'Скачать и установить новую версию?' : 'Скачивание доступно со страницы релиза в Настройках.';
    if (!updateDialog.open) updateDialog.showModal();
  }
}

async function refreshUpdateDisplay() {
  if (updateBusy) return;
  const generation = ++releasePaintGeneration;
  const result = await window.pywebview.api.check_for_update();
  if (!updateBusy && generation === releasePaintGeneration &&
      (result.failure || !updateResult || JSON.stringify(result) !== JSON.stringify(updateResult) ||
       result.prompts && result.version && promptedUpdate !== `v0.${result.version}`)) {
    paintRelease(result);
  }
}

async function showReleaseStatus() {
  const api = window.pywebview.api;
  $('app-version').textContent = await api.app_version();
  const refresh = () => refreshUpdateDisplay().catch(() => logAction('ui_error', 'release_status_failed'));
  refresh();
  window.setInterval(refresh, 5000);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') refresh();
  });
}

$('check-update').addEventListener('click', async () => {
  const button = $('check-update');
  button.disabled = true;
  $('settings-update-status').textContent = 'Проверяю наличие обновлений…';
  try {
    const result = await window.pywebview.api.refresh_update();
    if (!result.ok) throw Error('Не удалось проверить наличие обновлений.');
    if (!updateBusy) paintRelease(await window.pywebview.api.check_for_update());
  } catch (error) {
    if (!updateBusy) $('settings-update-status').textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

$('update-close').addEventListener('click', () => updateDialog.close());
$('never-prompt-update').addEventListener('click', async () => {
  const button = $('never-prompt-update');
  button.disabled = true;
  try {
    const result = await window.pywebview.api.dismiss_update_prompts();
    if (!result.ok) throw Error('Не удалось сохранить настройку уведомлений.');
    updateDialog.close();
  } catch (error) {
    $('update-dialog-status').textContent = error.message;
  } finally {
    button.disabled = false;
  }
});
updateDialog.addEventListener('cancel', event => {
  if (updateBusy) event.preventDefault();
});

async function installAvailableUpdate() {
  if (updateBusy) return;
  updateBusy = true;
  const actions = ['settings-install-update', 'dialog-install-update', 'never-prompt-update', 'update-close'];
  actions.forEach(id => { $(id).disabled = true; });
  const status = text => {
    $('settings-update-status').textContent = text;
    $('update-dialog-status').textContent = text;
  };
  try {
    const api = window.pywebview.api;
    const result = await api.install_update();
    if (!result.ok) throw Error(result.error);
    for (;;) {
      const progress = await api.update_progress();
      if (progress.phase === 'error') throw Error(progress.error);
      if (progress.phase !== 'idle') {
        paintUpdateProgress(progress);
        if (progress.phase === 'restart') {
          status('Обновление скачано. Устанавливаю и перезапускаю приложение…');
          return;
        }
        const percent = Math.floor(100 * progress.received / progress.total);
        status(percent >= 100 ? 'Проверяю и распаковываю обновление…' : `Скачиваю обновление · ${percent}%`);
      }
      await new Promise(resolve => setTimeout(resolve, 240));
    }
  } catch (error) {
    updateBusy = false;
    paintUpdateProgress(null);
    status(error.message || 'Не удалось установить обновление.');
    actions.forEach(id => { $(id).disabled = false; });
  }
}
$('dialog-install-update').addEventListener('click', installAvailableUpdate);
$('settings-install-update').addEventListener('click', installAvailableUpdate);

for (const group of navGroups) {
  group.querySelector('summary').addEventListener('click', () => closeNavigation(group));
}
document.addEventListener('click', event => {
  if (!event.target.closest('.nav-group')) closeNavigation();
});
document.addEventListener('keydown', event => {
  if (event.key !== 'Escape') return;
  const open = navGroups.find(group => group.open);
  if (open) {
    closeNavigation();
    open.querySelector('summary').focus();
  }
});
$('nav-program').addEventListener('change', () => {
  showProgramName();
  loadHomePlan();
});
$('course-back').addEventListener('click', () => showPage(courseReturnPage));
document.querySelectorAll('[data-page]').forEach(button => {
  button.addEventListener('click', () => {
    const page = button.classList.contains('quick-icon') && button.classList.contains('active') ? 'home' : button.dataset.page;
    showPage(page, button.hasAttribute('data-program') ? $('nav-program').value : '');
  });
});
document.querySelectorAll('[data-section]').forEach(button => {
  button.addEventListener('click', () => showResource(button.dataset.section, '', '', button.hasAttribute('data-program') ? $('nav-program').value : ''));
});
for (const [id, direction] of [['month-prev', -1], ['month-next', 1]]) {
  $(id).addEventListener('click', () => {
    visibleMonth = new Date(visibleMonth.getFullYear(), visibleMonth.getMonth() + direction, 1);
    loadMiniDay(new Date(visibleMonth.getFullYear(), visibleMonth.getMonth(), 1, 12));
    $(id).focus();
  });
}
$('notification-sounds').addEventListener('change', async event => {
  const input = event.target;
  input.disabled = true;
  try {
    const result = await window.pywebview.api.set_notification_sounds(input.checked);
    if (!result.ok) throw Error(result.error);
  } catch (error) {
    input.checked = !input.checked;
    showMessage(error.message);
  } finally {
    input.disabled = false;
  }
});
$('resource-program').addEventListener('change', () => showResource(activeResource));
$('curriculum-program').addEventListener('change', () => {
  selectHomeProgram($('curriculum-program').value);
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
  if (result.ok) { $('login-status').textContent = ''; await signedIn(result); }
  else $('login-status').textContent = result.error || 'Не удалось войти';
});
$('logout').addEventListener('click', async () => {
  await window.pywebview.api.logout();
  clearTimeout(cacheTimer);
  homeCacheOwner = '';
  cachedPrograms = [];
  cachedPanels = {};
  dayCache.clear();
  chosenDocument = null;
  renderDocumentInfo();
  renderDocumentResults();
  $('document-progress-box').classList.add('hidden');
  $('document-status').textContent = '';
  updateDocumentButtons();
  ++resourceVersion;
  ++topicVersion;
  ++conversationListVersion;
  ++curriculumVersion;
  ++accountVersion;
  ++progressVersion;
  ++planVersion;
  progressObserver.disconnect();
  progressCache.clear();
  homePlanCache.clear();
  sectionCache.clear();
  pendingProgress.clear();
  ++dayVersion;
  selectedDayEvents = null;
  selectedDay = new Date();
  visibleMonth = new Date(selectedDay.getFullYear(), selectedDay.getMonth(), 1);
  $('nav-program').replaceChildren();
  $('nav-program-label').classList.add('hidden');
  showProgramName();
  $('client-caption').classList.add('hidden');
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
function applyAppearance(theme) {
  const safe = ['night', 'cobalt', 'cream', 'pine', 'plum'].includes(theme) ? theme : 'night';
  document.body.dataset.theme = safe;
  document.body.classList.toggle('light', safe === 'cobalt' || safe === 'cream');
  $('theme-options').querySelector('input[value="' + safe + '"]').checked = true;
}
let savedAppearance = 'night';
$('theme-options').addEventListener('change', async (event) => {
  const target = event.target;
  const panel = $('theme-options');
  const status = $('theme-status');
  const inputs = [...panel.querySelectorAll('input')];
  const choice = target.value;
  applyAppearance(choice);
  inputs.forEach(input => { input.disabled = true; });
  status.textContent = '';
  try {
    const result = await window.pywebview.api.set_appearance(choice);
    if (!result.ok) throw Error();
    savedAppearance = choice;
  } catch {
    applyAppearance(savedAppearance);
    status.textContent = 'Не удалось сохранить тему.';
  } finally {
    inputs.forEach(input => { input.disabled = false; });
  }
});
$('autostart').addEventListener('change', async event => { const result = await window.pywebview.api.set_autostart(event.target.checked); if (!result.ok) { event.target.checked = !event.target.checked; showMessage(result.error); } });
window.setWindowMaximized = maximized => {
  const button = $('window-maximize');
  const label = maximized ? 'Восстановить размер' : 'Развернуть';
  button.textContent = maximized ? '❐' : '□';
  button.title = label;
  button.setAttribute('aria-label', label);
};
function windowCommand(command) {
  window.pywebview?.api?.window_command(command);
}
for (const [id, command] of [['window-minimize', 'minimize'], ['window-maximize', 'toggle_maximize'], ['window-close', 'close']]) {
  $(id).addEventListener('click', () => windowCommand(command));
}
for (const region of document.querySelectorAll('.pywebview-drag-region')) {
  region.addEventListener('dblclick', () => windowCommand('toggle_maximize'));
}
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
    row.dataset.conversationId = item.id;
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
  animateIn(box);
  const account = accountVersion;
  try {
    const marked = await window.pywebview.api.mark_conversation_read(item.id);
    if (account !== accountVersion) return;
    if (!marked.ok) throw Error(marked.error);
    item.unread = 0;
    const row = [...$('conversation-list').children].find(node => node.dataset.conversationId === String(item.id));
    if (row) row.textContent = item.name;
  } catch (error) {
    if (account === accountVersion && currentConversation === item) showMessage(error.message || 'Не удалось отметить переписку прочитанной');
  }
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

const documentGroups = {
  general: {
    indexes: [['plumb', 'Plumb Search', 'Публичный индекс сайтов · совпадения в поисковых выдержках.'],
      ['wiki', 'Википедия', 'Русская или английская Википедия · текст найденных страниц.']],
    services: [['Text.ru', 'Бесплатная проверка текста с лимитами.', 'https://text.ru/antiplagiat'],
      ['Advego Plagiatus', 'Бесплатная онлайн-проверка текста; есть отдельная программа.', 'https://advego.com/antiplagiat/'],
      ['eTXT', 'Есть бесплатная онлайн-проверка с ограничениями.', 'https://www.etxt.ru/antiplagiat/'],
      ['Content Watch', 'Бесплатная проверка текста без регистрации, с дневным лимитом.', 'https://content-watch.ru/text/'],
      ['DupliChecker', 'Есть бесплатная проверка текста или файла с лимитом.', 'https://www.duplichecker.com/']],
  },
  academic: {
    indexes: [['doaj', 'DOAJ', 'Открытые научные журналы · совпадения в аннотациях.'],
      ['wiki', 'Википедия', 'Справочные материалы · текст найденных страниц.']],
    services: [['Антиплагиат.ру', 'Тариф «Бесплатный»: открытый интернет и краткий отчёт. Требуется аккаунт.', 'https://antiplagiat.ru/'],
      ['Quetext', 'Бесплатный тариф для коротких текстов. Подходит для проверки фрагмента работы.', 'https://www.quetext.com/'],
      ['Руконтекст', 'Проверка через учебное заведение по билету преподавателя. Самостоятельная проверка платная.', 'https://text.rucont.ru/'],
      ['ВКР-ВУЗ.РФ', 'Требуется учётная запись от учебного заведения. Ссылка ведёт на инструкцию вуза.', 'https://kbsu.ru/wp-content/uploads/2016/04/Instruktsiya-dlya-obuchayushhihsya-i-prepodavatelej-na-platforme-VKR-VUZ.-RF.pdf', 'Открыть инструкцию ↗']],
  },
};
let chosenDocument = null;
let documentBusy = false;
const documentSize = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 1});
const selectedDocumentGroup = () => $('document-groups').querySelector('input:checked').value;

function checkLink(label, url, className='internal-link') {
  const button = element('button', className, label);
  button.type = 'button';
  button.addEventListener('click', async () => {
    try {
      const result = await window.pywebview.api.document_open_link(url);
      if (!result.ok) throw Error();
    } catch { showMessage('Не удалось открыть ссылку в браузере.'); }
  });
  return button;
}

function renderDocumentResults(results = [], running = false) {
  const list = $('document-checkers');
  const {indexes, services} = documentGroups[selectedDocumentGroup()];
  list.replaceChildren();
  for (const [id, name, help] of indexes) {
    const item = element('details', 'panel document-checker');
    const result = results.find(row => row.id === id);
    const count = result?.matches.length || 0;
    const status = result?.checked ? `Совпадения: ${count} из ${result.checked}`
      : result?.error || (running ? 'Ищу…' : 'Готов к проверке');
    const summary = element('summary', 'document-check-summary');
    summary.append(element('strong', '', name), element('span', 'document-badge', status));
    item.append(summary, element('p', 'muted document-note', help));
    if (result?.checked) {
      const meter = element('progress', 'document-meter');
      meter.max = result.checked;
      meter.value = count;
      meter.setAttribute('aria-label', `Фраз с совпадениями: ${count} из ${result.checked}`);
      item.append(meter);
      item.append(element('p', 'muted document-note', `Проверено фраз: ${result.checked} из ${result.sampled}.`));
    }
    if (result?.error && result.checked) item.append(element('p', 'muted document-note', result.error + '. Показаны частичные результаты.'));
    for (const match of result?.matches || []) {
      const row = element('div', 'document-match');
      row.append(element('blockquote', '', (match.page ? `Стр. ${match.page} · ` : '') + match.text));
      for (const source of match.sources) row.append(checkLink(source.title, source.url));
      item.append(row);
    }
    item.open = Boolean(count);
    list.append(item);
  }
  const links = $('document-services');
  links.replaceChildren();
  for (const [name, help, url, action = 'Открыть в браузере ↗'] of services) {
    const card = element('article', 'document-service');
    card.append(element('strong', '', name), element('small', 'muted', help), checkLink(action, url, 'ghost'));
    links.append(card);
  }
}

function updateDocumentButtons() {
  $('document-pick').disabled = documentBusy;
  $('document-groups').disabled = documentBusy;
  $('document-run').disabled = documentBusy || !chosenDocument;
}

function renderDocumentInfo() {
  const details = $('document-info');
  details.replaceChildren();
  details.classList.toggle('hidden', !chosenDocument);
  if (!chosenDocument) return;
  const bytes = chosenDocument.size;
  const size = bytes < 1024 ? documentSize.format(bytes) + ' Б'
    : bytes < 1024 * 1024 ? documentSize.format(bytes / 1024) + ' КБ'
      : documentSize.format(bytes / (1024 * 1024)) + ' МБ';
  for (const [label, value] of [['Файл', chosenDocument.name], ['Размер', size], ['Путь', chosenDocument.path]]) {
    details.append(element('dt', 'muted', label), element('dd', '', value));
  }
}

async function chooseDocument() {
  if (documentBusy) return;
  const account = accountVersion;
  documentBusy = true;
  updateDocumentButtons();
  const status = $('document-status');
  status.textContent = 'Читаю выбранный файл…';
  try {
    const result = await window.pywebview.api.document_pick();
    if (account !== accountVersion) return;
    if (!result.ok) throw Error(result.error);
    status.textContent = '';
    if (result.cancelled) return;
    chosenDocument = result.file;
    renderDocumentInfo();
    renderDocumentResults();
    $('document-progress-box').classList.add('hidden');
  } catch (error) {
    if (account === accountVersion) status.textContent = chosenDocument ? `${error.message} Предыдущий файл остаётся выбранным.` : error.message;
  } finally {
    documentBusy = false;
    updateDocumentButtons();
  }
}

function paintDocumentProgress(percent, label = `${percent}%`) {
  $('document-progress').value = percent;
  $('document-count').textContent = label;
}

$('document-groups').addEventListener('change', () => {
  $('document-status').textContent = '';
  $('document-progress-box').classList.add('hidden');
  renderDocumentResults();
  updateDocumentButtons();
});
$('document-pick').addEventListener('click', chooseDocument);
async function runDocumentCheck() {
  if (documentBusy || !chosenDocument) return;
  const account = accountVersion;
  documentBusy = true;
  updateDocumentButtons();
  const status = $('document-status');
  status.textContent = '';
  renderDocumentResults([], true);
  $('document-progress-box').classList.remove('hidden');
  paintDocumentProgress(0);
  const api = window.pywebview.api;
  let finished = false;
  let pollBusy = false;
  const timer = setInterval(async () => {
    if (pollBusy) return;
    pollBusy = true;
    try {
      const state = await api.document_check_progress();
      if (!finished && account === accountVersion && state.total) paintDocumentProgress(Math.floor(100 * state.completed / state.total));
    } catch { clearInterval(timer); }
    finally { pollBusy = false; }
  }, 260);
  try {
    const result = await api.document_check(selectedDocumentGroup());
    if (account !== accountVersion) return;
    if (!result.ok) throw Error(result.error);
    renderDocumentResults(result.results);
    const checked = result.results.some(source => source.checked);
    const partial = result.results.some(source => source.error);
    paintDocumentProgress(100, checked ? (partial ? 'Завершён с ограничениями' : 'Готово · 100%') : 'Нет результатов');
  } catch (error) {
    if (account === accountVersion) {
      renderDocumentResults();
      paintDocumentProgress(0, 'Прервано');
      status.textContent = error.message;
    }
  } finally {
    finished = true;
    clearInterval(timer);
    documentBusy = false;
    updateDocumentButtons();
  }
}
$('document-run').addEventListener('click', runDocumentCheck);
renderDocumentResults();
updateDocumentButtons();
