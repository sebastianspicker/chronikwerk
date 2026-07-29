const views = [...document.querySelectorAll('[data-view]')];
const routeButtons = [...document.querySelectorAll('[data-route]')];

function showView(route) {
  views.forEach((view) => {
    view.hidden = view.dataset.view !== route;
  });
  routeButtons.forEach((button) => {
    if (!button.closest('.primary-nav')) return;
    if (button.dataset.route === route) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  history.replaceState(null, '', route === 'overview' ? '#overview' : `#${route}`);
  document.querySelector(`[data-view="${route}"] h1`)?.focus({preventScroll: true});
}

routeButtons.forEach((button) => {
  button.addEventListener('click', () => showView(button.dataset.route ?? 'overview'));
});

document.querySelector('[data-simulate-storage]')?.addEventListener('click', (event) => {
  const button = event.currentTarget;
  document.querySelector('[data-storage-state]').textContent = 'Fixture check passed';
  document.querySelector('[data-storage-result]').textContent = 'Simulated. No storage request was sent.';
  button.disabled = true;
});

const filterForm = document.querySelector('[data-job-filter]');
filterForm?.addEventListener('submit', (event) => {
  event.preventDefault();
  const data = new FormData(filterForm);
  const ticket = String(data.get('ticket') ?? '').trim().toLowerCase();
  const status = String(data.get('status') ?? '');
  let visible = 0;
  document.querySelectorAll('[data-job-rows] tr').forEach((row) => {
    const matches = (!ticket || row.dataset.ticket?.includes(ticket)) && (!status || row.dataset.status === status);
    row.hidden = !matches;
    if (matches) visible += 1;
  });
  document.querySelector('[data-filter-result]').textContent = `${visible} fixture ${visible === 1 ? 'event' : 'events'} shown.`;
});

document.querySelector('[data-show-job]')?.addEventListener('click', () => {
  const detail = document.querySelector('[data-job-detail]');
  detail.hidden = false;
  detail.scrollIntoView({behavior: 'smooth', block: 'start'});
});

document.querySelector('[data-hide-job]')?.addEventListener('click', () => {
  document.querySelector('[data-job-detail]').hidden = true;
  document.querySelector('[data-show-job]')?.focus();
});

const retryAck = document.querySelector('[data-retry-ack]');
const retryButton = document.querySelector('[data-simulate-retry]');
retryAck?.addEventListener('change', () => {
  retryButton.disabled = !retryAck.checked;
});
retryButton?.addEventListener('click', () => {
  document.querySelector('[data-retry-result]').textContent = 'Simulated. Reprocessing was not requested.';
  retryButton.disabled = true;
  retryAck.disabled = true;
});

const configForm = document.querySelector('[data-config-form]');
const configFields = [...document.querySelectorAll('[data-config-path]')];
function configChanges() {
  return configFields.flatMap((field) => {
    const control = field.querySelector('input, select');
    if (!control || control.value === field.dataset.original) return [];
    return [{path: field.dataset.configPath, before: field.dataset.original, after: control.value}];
  });
}

function updateChangeCount() {
  const count = configChanges().length;
  document.querySelector('[data-change-count]').textContent = count === 0 ? 'No changes' : `${count} ${count === 1 ? 'change' : 'changes'}`;
}
configFields.forEach((field) => field.querySelector('input, select')?.addEventListener('input', updateChangeCount));

configForm?.addEventListener('submit', (event) => {
  event.preventDefault();
  const changes = configChanges();
  const review = document.querySelector('[data-config-review]');
  const body = document.querySelector('[data-config-diff]');
  body.replaceChildren();
  changes.forEach((change) => {
    const row = document.createElement('tr');
    [change.path, change.before, change.after].forEach((value) => {
      const cell = document.createElement('td');
      const code = document.createElement('code');
      code.textContent = value;
      cell.append(code);
      row.append(cell);
    });
    body.append(row);
  });
  review.hidden = changes.length === 0;
  if (changes.length > 0) review.scrollIntoView({behavior: 'smooth', block: 'start'});
});

const stageAck = document.querySelector('[data-stage-ack]');
const stageButton = document.querySelector('[data-simulate-stage]');
stageAck?.addEventListener('change', () => {
  stageButton.disabled = !stageAck.checked;
});
stageButton?.addEventListener('click', () => {
  document.querySelector('[data-stage-result]').textContent = 'Simulated. No revision was staged and no restart was requested.';
  stageButton.disabled = true;
  stageAck.disabled = true;
});

const initialRoute = location.hash.slice(1);
showView(['jobs', 'configuration'].includes(initialRoute) ? initialRoute : 'overview');
