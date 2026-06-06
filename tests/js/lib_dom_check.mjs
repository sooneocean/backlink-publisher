// Node-level check for the security-critical pure helpers in lib/dom.js.
// No JS test framework this round — run with:  node tests/js/lib_dom_check.mjs
// (renderBadge needs a DOM, so it is verified in the browser walkthrough; this
// covers esc(), the 5-char escape that guards the new shared rendering path.)
import { esc } from '../../webui_app/static/js/lib/dom.js';
import assert from 'node:assert/strict';

// 5-char superset, including the single quote (the gap channel-binding's
// 4-char escapeHtml left open).
assert.equal(esc('\'"<>&'), '&#39;&quot;&lt;&gt;&amp;', 'esc must escape all 5 chars incl single-quote');

// XSS payload renders inert (no live markup survives).
const payload = esc('"><img src=x onerror=alert(1)>');
assert.ok(!/<img/i.test(payload), 'esc must neutralise an <img> payload');

// nullish -> empty string, never "null"/"undefined".
assert.equal(esc(null), '');
assert.equal(esc(undefined), '');

console.log('lib/dom.js esc(): all checks passed');
