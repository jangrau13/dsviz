/* Search on ctrl/cmd + K, the shortcut the editor uses. */
document.addEventListener('keydown', function (e) {
  if (!(e.metaKey || e.ctrlKey) || e.key.toLowerCase() !== 'k') return;
  var box = document.querySelector('.md-search__input');
  if (!box) return;
  e.preventDefault();
  var toggle = document.getElementById('__search');
  if (toggle) toggle.checked = true;   // opens it on a narrow screen
  box.focus();
  box.select();
});
document.addEventListener('DOMContentLoaded', function () {
  var box = document.querySelector('.md-search__input');
  if (!box) return;
  var mac = /Mac|iPhone|iPad/.test(navigator.platform);
  box.placeholder = mac ? 'Search  ⌘K' : 'Search  Ctrl+K';
});
