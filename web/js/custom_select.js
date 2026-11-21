/**
 * Bespoke Swiss custom select UI:
 * - Mobile (<= 1080px): smooth slide-up Bottom Sheet with 48px touch rows & checkmark
 * - Desktop (> 1080px): crisp hairline anchored popover with keyboard navigation
 * Keeps underlying <select> intact so all existing listeners, values & tests work seamlessly.
 */

const SELECT_IDS = [
  "cfg-deployment-select",
  "cfg-heater-select",
  "hl-deployment-select",
  "hl-heater-select",
  "cc-sku-select",
  "cc-site-select",
  "cc-workload-select",
  "cc-telemetry-site-filter",
];

let backdropEl = null;
let sheetEl = null;
let activeCtx = null;

function ensureOverlay() {
  if (backdropEl && sheetEl) return;

  backdropEl = document.createElement("div");
  backdropEl.className = "cg-sheet-backdrop";
  backdropEl.hidden = true;
  backdropEl.addEventListener("click", closeSheet);

  sheetEl = document.createElement("div");
  sheetEl.className = "cg-sheet";
  sheetEl.setAttribute("role", "listbox");
  sheetEl.hidden = true;

  document.body.appendChild(backdropEl);
  document.body.appendChild(sheetEl);

  document.addEventListener("keydown", (e) => {
    if (!activeCtx) return;
    if (e.key === "Escape") {
      e.preventDefault();
      closeSheet();
    }
  });
  window.addEventListener("resize", () => {
    if (activeCtx) closeSheet();
  });
}

function closeSheet() {
  if (activeCtx) {
    activeCtx.trigger.setAttribute("aria-expanded", "false");
    activeCtx.trigger.classList.remove("is-open");
    activeCtx = null;
  }
  if (backdropEl) {
    backdropEl.classList.remove("is-visible");
    backdropEl.hidden = true;
  }
  if (sheetEl) {
    sheetEl.classList.remove("is-visible", "is-popover");
    sheetEl.hidden = true;
    sheetEl.style.top = "";
    sheetEl.style.left = "";
    sheetEl.style.width = "";
  }
}

function openSheet(ctx) {
  ensureOverlay();
  if (activeCtx === ctx) {
    closeSheet();
    return;
  }
  if (activeCtx) closeSheet();

  const { select, trigger, labelText } = ctx;
  const opts = Array.from(select.options || []);
  if (!opts.length) return;

  activeCtx = ctx;
  trigger.setAttribute("aria-expanded", "true");
  trigger.classList.add("is-open");

  const isMobile = window.innerWidth <= 1080;
  sheetEl.replaceChildren();

  if (isMobile) {
    const handle = document.createElement("div");
    handle.className = "cg-sheet-handle";
    sheetEl.appendChild(handle);

    const head = document.createElement("div");
    head.className = "cg-sheet-head";
    const title = document.createElement("strong");
    title.textContent = labelText || "Select option";
    const doneBtn = document.createElement("button");
    doneBtn.type = "button";
    doneBtn.className = "cg-sheet-close";
    doneBtn.textContent = "Close";
    doneBtn.addEventListener("click", closeSheet);
    head.append(title, doneBtn);
    sheetEl.appendChild(head);
  }

  const list = document.createElement("div");
  list.className = "cg-sheet-list";

  for (const opt of opts) {
    const isSel = opt.value === select.value;
    const item = document.createElement("button");
    item.type = "button";
    item.className = `cg-sheet-opt${isSel ? " is-selected" : ""}`;
    item.setAttribute("role", "option");
    item.setAttribute("aria-selected", isSel ? "true" : "false");

    const txtSpan = document.createElement("span");
    txtSpan.className = "cg-sheet-opt-label";
    txtSpan.textContent = opt.textContent || opt.label || opt.value;

    const checkSpan = document.createElement("span");
    checkSpan.className = "cg-sheet-opt-check";
    checkSpan.setAttribute("aria-hidden", "true");
    checkSpan.textContent = isSel ? "\u2713" : "";

    item.append(txtSpan, checkSpan);
    item.addEventListener("click", () => {
      if (select.value !== opt.value) {
        select.value = opt.value;
        select.dispatchEvent(new Event("change", { bubbles: true }));
      }
      ctx.syncLabel();
      closeSheet();
    });
    list.appendChild(item);
  }
  sheetEl.appendChild(list);

  backdropEl.hidden = false;
  sheetEl.hidden = false;

  if (isMobile) {
    sheetEl.classList.remove("is-popover");
    sheetEl.style.top = "";
    sheetEl.style.left = "";
    sheetEl.style.width = "";
  } else {
    sheetEl.classList.add("is-popover");
    const rect = trigger.getBoundingClientRect();
    const width = Math.max(rect.width, 240);
    const maxH = 300;
    const spaceBelow = window.innerHeight - rect.bottom - 12;
    const topPos =
      spaceBelow >= 180
        ? rect.bottom + 6
        : Math.max(12, rect.top - Math.min(maxH, opts.length * 42 + 12) - 6);
    const leftPos = Math.min(rect.left, window.innerWidth - width - 12);
    sheetEl.style.top = `${Math.round(topPos)}px`;
    sheetEl.style.left = `${Math.max(12, Math.round(leftPos))}px`;
    sheetEl.style.width = `${Math.round(width)}px`;
  }

  requestAnimationFrame(() => {
    backdropEl.classList.add("is-visible");
    sheetEl.classList.add("is-visible");
    const selectedItem = list.querySelector(".is-selected");
    if (selectedItem) {
      selectedItem.scrollIntoView({ block: "nearest" });
    }
  });
}

function enhanceSelect(select) {
  if (!select || select.dataset.cgCustomSelect === "1") return null;
  select.dataset.cgCustomSelect = "1";

  const wrapper = document.createElement("div");
  wrapper.className = "cg-select-wrap";
  if (select.classList.contains("cg-inline-select") || select.classList.contains("hl-site-select")) {
    wrapper.classList.add("is-compact");
  }

  const parent = select.parentNode;
  parent.insertBefore(wrapper, select);
  wrapper.appendChild(select);
  select.classList.add("cg-native-select-sr");

  const trigger = document.createElement("button");
  trigger.type = "button";
  trigger.className = "cg-select-trigger";
  trigger.setAttribute("aria-haspopup", "listbox");
  trigger.setAttribute("aria-expanded", "false");

  const valEl = document.createElement("span");
  valEl.className = "cg-select-value";

  const chevEl = document.createElement("span");
  chevEl.className = "cg-select-chevron";
  chevEl.setAttribute("aria-hidden", "true");
  chevEl.innerHTML =
    '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 6l4 4 4-4"/></svg>';

  trigger.append(valEl, chevEl);
  wrapper.appendChild(trigger);

  const labelEl = select.closest("label")?.querySelector("span");
  const labelText =
    labelEl?.textContent?.trim() || select.getAttribute("aria-label") || "Choose option";

  const syncLabel = () => {
    const selOpt = select.options[select.selectedIndex];
    valEl.textContent = selOpt ? selOpt.textContent : "Select...";
  };

  // Intercept programmatic .value updates via property observer polling only when needed + change + MutationObserver
  const ctx = { select, trigger, labelText, syncLabel };

  trigger.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    openSheet(ctx);
  });

  select.addEventListener("change", syncLabel);

  const mo = new MutationObserver(() => {
    syncLabel();
  });
  mo.observe(select, { childList: true, subtree: true, characterData: true, attributes: true });

  // Also patch the instance value setter so programmatic `select.value = x` immediately updates the trigger!
  const protoDesc = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value");
  if (protoDesc && protoDesc.set && protoDesc.get) {
    Object.defineProperty(select, "value", {
      configurable: true,
      get() {
        return protoDesc.get.call(this);
      },
      set(v) {
        protoDesc.set.call(this, v);
        syncLabel();
      },
    });
  }

  syncLabel();
  return ctx;
}

export function initCustomSelects() {
  const enhanced = [];
  for (const id of SELECT_IDS) {
    const el = document.getElementById(id);
    const ctx = enhanceSelect(el);
    if (ctx) enhanced.push(ctx);
  }
  return {
    syncAll: () => enhanced.forEach((c) => c.syncLabel()),
    close: closeSheet,
  };
}
