"use strict";
/* Copy, two ways — the same behaviour as pipe.online, because the dashboard
   is the same brand (Sam, 2026-09-01: "it is a core brand asset"):

   1. Clicking a code block copies it — whole block, one click, no button;
      prompt/comment spans are stripped. The feedback is the green
      "Copied ✓" tick.
   2. Highlighting text copies the highlight, same tick; a selection inside
      a block copies the selection raw. Fields are left alone.

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
    clone.querySelectorAll(".p, .c").forEach(function (n) { n.remove(); });
    return clone.textContent.split("\n").map(function (l) {
      return l.replace(/\s+$/, "");
    }).join("\n").replace(/^\s*\n/, "").trim();
  }

  var tickEl = null, tickFade = 0;
  function showTick(x, y) {
    if (!tickEl) {
      tickEl = document.createElement("span");
      tickEl.className = "copy-tick";
      tickEl.textContent = "Copied ✓";
      tickEl.setAttribute("aria-live", "polite");
      document.body.appendChild(tickEl);
    }
    tickEl.hidden = false;
    var w = tickEl.offsetWidth || 72, h = tickEl.offsetHeight || 22;
    var px = Math.min(Math.max(x - w / 2, 8), window.innerWidth - w - 8);
    var py = y - h - 10;
    tickEl.style.left = (px + window.scrollX) + "px";
    tickEl.style.top = ((py < 8 ? y + 12 : py) + window.scrollY) + "px";
    clearTimeout(tickFade);
    tickFade = setTimeout(function () { tickEl.hidden = true; }, DONE_MS);
  }

  function wireBlocks() {
    document.querySelectorAll("pre").forEach(function (pre) {
      if (pre.dataset.copyWired || !pre.textContent.trim()) return;
      pre.dataset.copyWired = "1";
      pre.classList.add("has-copy");
      pre.title = "click to copy";
      pre.addEventListener("click", function (e) {
        var s = document.getSelection();
        if (s && s.toString()) return; // a drag is the selection path's
        writeClipboard(blockText(pre)).then(function () {
          showTick(e.clientX, e.clientY);
        }, function () {});
      });
    });
  }

  function wireSelection() {
    var last = "";
    var grab = function () {
      var sel = document.getSelection();
      var text = sel ? sel.toString().trim() : "";
      if (!text || !sel.rangeCount) { last = ""; return; }
      var a = sel.anchorNode instanceof Element ? sel.anchorNode : sel.anchorNode && sel.anchorNode.parentElement;
      if (!a || a.closest("input, textarea, [contenteditable]")) return;
      if (text === last) return;
      var rect = sel.getRangeAt(0).getBoundingClientRect();
      if (!rect.width && !rect.height) return;
      last = text;
      writeClipboard(text).then(function () {
        showTick(rect.right, rect.top);
      }, function () {});
    };
    document.addEventListener("pointerup", function () { setTimeout(grab, 0); });
    document.addEventListener("keyup", function (e) {
      if (e.shiftKey || e.key === "a") setTimeout(grab, 0);
    });
    document.addEventListener("selectionchange", function () {
      var s = document.getSelection();
      if (!s || !s.toString()) last = "";
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
