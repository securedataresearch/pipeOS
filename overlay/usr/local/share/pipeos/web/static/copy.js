"use strict";
/* Copy, two ways — the same behaviour as pipe.online, because the dashboard
   is the same brand (Sam, 2026-09-01: "it is a core brand asset"):

   1. Every code block grows a corner "copy" button. Prompt (`.p`) and
      comment (`.c`) spans are stripped so a terminal quote copies what you
      would type; the boot report copies verbatim.
   2. Highlighting text copies it, and a green "Copied ✓" floats at the
      selection — no button, no second click. Fields and code blocks are
      left alone (the field has its own clipboard; the block has its button).

   Standalone on purpose: app.js re-renders whole views, so this watches the
   DOM rather than being called from every render site. Keep in step with
   web/src/core/copy.ts in the pipe repo. */
(function () {
  var DONE_MS = 1400;

  function writeClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText)
      return navigator.clipboard.writeText(text);
    return new Promise(function (resolve, reject) {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.cssText = "position:fixed;top:0;left:0;opacity:0";
      document.body.appendChild(ta);
      ta.select();
      var ok = document.execCommand("copy");
      ta.remove();
      ok ? resolve() : reject(new Error("copy failed"));
    });
  }

  function blockText(pre) {
    var clone = pre.cloneNode(true);
    clone.querySelectorAll(".copy-btn, .p, .c").forEach(function (n) { n.remove(); });
    return clone.textContent.split("\n").map(function (l) {
      return l.replace(/\s+$/, "");
    }).join("\n").replace(/^\s*\n/, "").trim();
  }

  function wireBlocks() {
    document.querySelectorAll("pre").forEach(function (pre) {
      if (pre.querySelector(".copy-btn") || !pre.textContent.trim()) return;
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "copy-btn";
      btn.textContent = "copy";
      btn.setAttribute("aria-label", "copy this block");
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        e.stopPropagation();
        writeClipboard(blockText(pre)).then(function () {
          btn.textContent = "copied";
          btn.classList.add("done");
          setTimeout(function () {
            btn.textContent = "copy";
            btn.classList.remove("done");
          }, DONE_MS);
        }, function () {});
      });
      pre.classList.add("has-copy");
      pre.appendChild(btn);
    });
  }

  function wireSelection() {
    var tick = document.createElement("span");
    tick.className = "copy-tick";
    tick.textContent = "Copied ✓";
    tick.hidden = true;
    tick.setAttribute("aria-live", "polite");
    document.body.appendChild(tick);
    var last = "", fade = 0;
    var hide = function () { tick.hidden = true; last = ""; };
    var grab = function () {
      var sel = document.getSelection();
      var text = sel ? sel.toString().trim() : "";
      if (!text || !sel.rangeCount) return hide();
      var a = sel.anchorNode instanceof Element ? sel.anchorNode : sel.anchorNode && sel.anchorNode.parentElement;
      if (!a || a.closest("input, textarea, [contenteditable], pre")) return hide();
      if (text === last) return;
      var rect = sel.getRangeAt(0).getBoundingClientRect();
      if (!rect.width && !rect.height) return hide();
      last = text;
      writeClipboard(text).then(function () {
        tick.hidden = false;
        var w = tick.offsetWidth || 72;
        var x = Math.min(Math.max(rect.right - w, 8), window.innerWidth - w - 8);
        var y = rect.top - tick.offsetHeight - 6;
        tick.style.left = (x + window.scrollX) + "px";
        tick.style.top = ((y < 8 ? rect.bottom + 6 : y) + window.scrollY) + "px";
        clearTimeout(fade);
        fade = setTimeout(function () { tick.hidden = true; }, DONE_MS);
      }, hide);
    };
    document.addEventListener("pointerup", function () { setTimeout(grab, 0); });
    document.addEventListener("keyup", function (e) {
      if (e.shiftKey || e.key === "a") setTimeout(grab, 0);
    });
    document.addEventListener("selectionchange", function () {
      var s = document.getSelection();
      if (!s || !s.toString()) hide();
    });
  }

  wireBlocks();
  wireSelection();
  if ("MutationObserver" in window) {
    var queued = false;
    new MutationObserver(function () {
      if (queued) return;
      queued = true;
      setTimeout(function () { queued = false; wireBlocks(); }, 100);
    }).observe(document.body, { childList: true, subtree: true });
  }
})();
