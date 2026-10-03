(() => {
  "use strict";

  const byData = (name) => document.querySelector(`[data-${name}]`);
  const announce = (element, message, variant = "success") => {
    element.textContent = message;
    element.className = `inline-result banner banner--${variant}`;
  };

  const initOverview = () => {
    const button = byData("storage-check");
    if (!button) return;
    const state = byData("storage-state");
    const time = byData("storage-time");
    const result = byData("storage-result");
    button.addEventListener("click", () => {
      state.textContent = "Check passed";
      state.className = "state-value state-value--success";
      time.hidden = false;
      time.textContent = "Synthetic check completed just now";
      result.textContent = "Storage demonstration check passed. No filesystem was accessed.";
      button.disabled = true;
      button.textContent = "Storage checked";
    });
  };

  const initJobs = () => {
    const form = byData("jobs-filter");
    if (!form) return;
    const rows = [...document.querySelectorAll("[data-jobs-rows] tr")];
    const empty = byData("jobs-empty");
    const count = byData("job-count");
    const filter = () => {
      const ticket = form.elements.ticket.value.trim();
      const status = form.elements.status.value;
      let visible = 0;
      rows.forEach((row) => {
        const matches = (!ticket || row.dataset.ticket === ticket) && (!status || row.dataset.status === status);
        row.hidden = !matches;
        if (matches) visible += 1;
      });
      empty.hidden = visible !== 0;
      count.textContent = `${visible} sample event${visible === 1 ? "" : "s"}`;
    };
    form.addEventListener("submit", (event) => { event.preventDefault(); filter(); });
    form.addEventListener("reset", () => window.setTimeout(filter, 0));
    form.addEventListener("input", filter);
    form.addEventListener("change", filter);
  };

  const initJob = () => {
    const form = byData("demo-retry");
    if (!form) return;
    const acknowledgement = form.querySelector("input[type=checkbox]");
    const button = form.querySelector("button[type=submit]");
    const timeline = byData("job-timeline");
    const result = byData("retry-result");
    acknowledgement.addEventListener("change", () => { button.disabled = !acknowledgement.checked; });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!acknowledgement.checked || form.dataset.completed) return;
      const item = document.createElement("li");
      item.className = "chronicle__entry chronicle__entry--pending demo-new-event";
      item.innerHTML = '<div class="chronicle__when"><time>Just now</time></div><div class="chronicle__what"><p class="chronicle__tags"><span class="notation notation--pending">Accepted</span></p><p class="chronicle__message">Synthetic retry admitted to this page’s process-local demonstration queue; not completed.</p><p class="chronicle__ref"><span class="visually-hidden">Request ID: </span><code>req-demo-4813-r2</code></p></div>';
      timeline.prepend(item);
      form.dataset.completed = "true";
      button.disabled = true;
      button.textContent = "Retry admission simulated";
      announce(result, "Synthetic retry accepted. This is an in-page event only and does not prove archival completion.");
    });
  };

  const initConfiguration = () => {
    const form = byData("demo-config");
    if (!form) return;
    const fields = [...form.querySelectorAll(".config-field")];
    const acknowledgement = document.querySelector("#config-ack");
    const reviewButton = byData("config-review");
    const panel = byData("config-review-panel");
    const diff = byData("config-diff");
    const stage = byData("config-stage");
    const count = byData("config-count");
    const result = byData("config-result");
    const staged = byData("config-staged");
    let reviewed = null;
    const changes = () => fields.map((field) => {
      const input = field.querySelector("input, select");
      return { path: field.dataset.path, before: field.dataset.original, after: input.value };
    }).filter((change) => change.before !== change.after);
    const sync = () => {
      const dirty = changes();
      count.textContent = dirty.length ? `${dirty.length} change${dirty.length === 1 ? "" : "s"} to review` : "No changes";
      fields.forEach((field) => {
        const changed = String(dirty.some((change) => change.path === field.dataset.path));
        field.dataset.changed = changed;
        field.querySelector("input, select").parentElement.dataset.changed = changed;
      });
      const reset = byData("config-reset");
      if (reset) reset.disabled = dirty.length === 0;
      reviewButton.disabled = dirty.length === 0;
      stage.disabled = dirty.length === 0 || !acknowledgement.checked
        || reviewed !== JSON.stringify(dirty);
      return dirty;
    };
    const render = () => {
      const dirty = changes();
      reviewed = JSON.stringify(dirty);
      sync();
      result.textContent = "";
      diff.replaceChildren();
      dirty.forEach((change) => {
        const row = document.createElement("tr");
        [change.path, change.before, change.after].forEach((value, index) => {
          const cell = document.createElement("td");
          cell.dataset.label = ["Path", "Before", "After"][index];
          const code = document.createElement("code");
          code.textContent = value;
          cell.append(code);
          row.append(cell);
        });
        diff.append(row);
      });
      panel.hidden = dirty.length === 0;
      if (dirty.length) {
        panel.tabIndex = -1;
        panel.focus({ preventScroll: true });
        panel.scrollIntoView({ block: "nearest" });
      }
    };
    const invalidateReview = () => {
      if (reviewed !== null) {
        announce(result, "Values changed. Review the current changes before staging.", "warning");
      }
      reviewed = null;
      staged.hidden = true;
      sync();
    };
    fields.forEach((field) => {
      const input = field.querySelector("input, select");
      input.addEventListener("input", invalidateReview);
      input.addEventListener("change", invalidateReview);
    });
    byData("config-reset")?.addEventListener("click", () => {
      fields.forEach((field) => { field.querySelector("input, select").value = field.dataset.original; });
      acknowledgement.checked = false;
      invalidateReview();
      panel.hidden = true;
      result.textContent = "";
    });
    acknowledgement.addEventListener("change", sync);
    reviewButton.addEventListener("click", render);
    stage.addEventListener("click", () => {
      sync();
      if (stage.disabled) return;
      staged.hidden = false;
      staged.textContent = "Synthetic revision staged: demo-31aug-0942. Activation would require an external restart; this static page has not changed any service.";
      announce(result, "Synthetic staging complete. Review remains visible for this browser session.");
      reviewed = null;
      sync();
    });
    sync();
  };

  const initRevisions = () => {
    const dialog = byData("restore-dialog");
    if (!dialog) return;
    const form = byData("restore-form");
    const acknowledgement = document.querySelector("#restore-ack");
    const submit = form.querySelector("button[type=submit]");
    const revision = byData("restore-revision");
    const result = byData("restore-result");
    document.querySelectorAll("[data-demo-restore]").forEach((button) => button.addEventListener("click", () => {
      revision.textContent = button.dataset.revision;
      acknowledgement.checked = false;
      submit.disabled = true;
      dialog.showModal();
    }));
    acknowledgement.addEventListener("change", () => { submit.disabled = !acknowledgement.checked; });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      dialog.close();
      announce(result, `Synthetic restore review accepted for ${revision.textContent}. No configuration was changed; a real restore would create a staged revision and require restart.`);
    });
    document.querySelectorAll("[data-dialog-close]").forEach((button) => button.addEventListener("click", () => dialog.close()));
  };

  initOverview();
  initJobs();
  initJob();
  initConfiguration();
  initRevisions();
})();
