// static/js/front.js —— 前台交互（原生 JS，无任何第三方库）
/**
 * 本文件包含：
 * 1. 导航滚动阴影、移动端菜单折叠
 * 2. flash 提示 4 秒自动消失
 * 3. 表单提交防重复（禁用按钮 + "正在提交…"）
 * 4. 删除 / 取消等操作的二次确认
 * 5. 日期联动（开始日期不早于今天，结束日期不早于开始日期，并提示共 N 晚 / N 小时）
 * 6. 首页快捷购票：选影片 + 选日期 -> 跳到影片详情页
 * 7. 支付倒计时（第 4 步使用）
 * 8. 选座 / 实时报价的初始化入口（第 4 步填充实现；找不到对应元素直接返回）
 *
 * 约定：所有写请求都要带 CSRF 令牌（从 <meta name="csrf-token"> 读取）。
 */
(function () {
  'use strict';

  // ---------------- 小工具 ----------------
  function $(selector, root) {
    return (root || document).querySelector(selector);
  }

  function $all(selector, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(selector));
  }

  /** CSRF 令牌（app.py 里 before_request 统一校验） */
  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
  }

  /** 分 -> "128.00" */
  function formatYuan(fen) {
    var n = Number(fen || 0) / 100;
    return n.toFixed(2);
  }

  /** 统一的 POST 封装：自动带 CSRF 头，返回 JSON */
  function postJSON(url, data) {
    return fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': csrfToken()
      },
      body: JSON.stringify(data || {})
    }).then(function (resp) {
      return resp.json().catch(function () {
        return { ok: false, message: '服务器返回了无法解析的内容' };
      }).then(function (body) {
        if (!resp.ok && !body.message) {
          body.ok = false;
          body.message = '请求失败（' + resp.status + '）';
        }
        return body;
      });
    });
  }

  // 暴露给后续页面脚本复用
  window.GY = {
    csrfToken: csrfToken,
    formatYuan: formatYuan,
    postJSON: postJSON
  };

  // ---------------- 1. 导航 ----------------
  function initNavbar() {
    var navbar = $('#frontNavbar');
    if (navbar) {
      var onScroll = function () {
        navbar.classList.toggle('scrolled', window.scrollY > 12);
      };
      onScroll();
      window.addEventListener('scroll', onScroll, { passive: true });
    }

    var toggle = $('#navToggle');
    var menu = $('#navMenu');
    if (toggle && menu) {
      toggle.addEventListener('click', function () {
        var opened = menu.classList.toggle('open');
        var icon = toggle.querySelector('i');
        if (icon) {
          icon.className = opened ? 'bi bi-x-lg' : 'bi bi-list';
        }
      });
      // 点击菜单项后收起
      $all('a', menu).forEach(function (link) {
        link.addEventListener('click', function () {
          menu.classList.remove('open');
          var icon = toggle.querySelector('i');
          if (icon) { icon.className = 'bi bi-list'; }
        });
      });
    }
  }

  // ---------------- 2. flash 自动消失 ----------------
  function initFlash() {
    var wrap = $('#flashWrap');
    if (!wrap) { return; }
    $all('.flash-item', wrap).forEach(function (item, index) {
      setTimeout(function () {
        item.classList.add('hide');
        setTimeout(function () {
          if (item.parentNode) { item.parentNode.removeChild(item); }
          if (wrap && !wrap.children.length && wrap.parentNode) {
            wrap.parentNode.removeChild(wrap);
          }
        }, 420);
      }, 4000 + index * 260);
    });
  }

  // ---------------- 3. 表单防重复提交 ----------------
  function initFormGuard() {
    var forms = $all('form').filter(function (form) {
      var method = (form.getAttribute('method') || 'get').toLowerCase();
      return method === 'post' && !form.hasAttribute('data-no-guard');
    });

    forms.forEach(function (form) {
      form.addEventListener('submit', function (evt) {
        if (form.dataset.submitting === '1') {
          // 已经在提交，直接拦住第二次
          evt.preventDefault();
          return;
        }
        form.dataset.submitting = '1';
        var buttons = $all('button[type="submit"], input[type="submit"]', form);
        buttons.forEach(function (btn) {
          btn.disabled = true;
          if (btn.tagName === 'BUTTON') {
            if (!btn.dataset.originalHtml) { btn.dataset.originalHtml = btn.innerHTML; }
            btn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> 正在提交…';
          } else {
            btn.value = '正在提交…';
          }
        });
      });
    });

    // 从浏览器往返缓存回来时恢复按钮，避免"永远在提交中"
    window.addEventListener('pageshow', function () {
      forms.forEach(function (form) {
        form.dataset.submitting = '0';
        $all('button[type="submit"], input[type="submit"]', form).forEach(function (btn) {
          btn.disabled = false;
          if (btn.tagName === 'BUTTON' && btn.dataset.originalHtml) {
            btn.innerHTML = btn.dataset.originalHtml;
          }
        });
      });
    });
  }

  // ---------------- 4. 二次确认 ----------------
  function initConfirm() {
    // 4.1 普通链接 / 按钮：<a data-confirm="确定要退票吗？">
    $all('[data-confirm]').forEach(function (el) {
      if (el.tagName === 'FORM') { return; }
      el.addEventListener('click', function (evt) {
        var text = el.getAttribute('data-confirm') || '确定要执行这个操作吗？';
        if (!window.confirm(text)) {
          evt.preventDefault();
          evt.stopPropagation();
        }
      });
    });

    // 4.2 表单：<form data-confirm="确定要取消这个场次吗？">
    $all('form[data-confirm]').forEach(function (form) {
      form.addEventListener('submit', function (evt) {
        var text = form.getAttribute('data-confirm') || '确定要执行这个操作吗？';
        if (!window.confirm(text)) {
          evt.preventDefault();
          form.dataset.submitting = '0';
        }
      });
    });
  }

  // ---------------- 5. 日期联动 ----------------
  function initDateRange() {
    var start = $('[data-date-start]');
    var end = $('[data-date-end]');
    if (!start) { return; }

    var today = new Date();
    var pad = function (n) { return (n < 10 ? '0' : '') + n; };
    var toValue = function (d) {
      return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
    };

    // 开始日期不能早于今天
    if (!start.min) { start.min = toValue(today); }

    var hint = $('[data-date-hint]');
    var unit = (hint && hint.getAttribute('data-unit')) || '晚';

    function sync() {
      if (!end) { return; }
      // 结束日期不早于开始日期
      if (start.value) { end.min = start.value; }
      if (start.value && end.value && end.value < start.value) {
        end.value = start.value;
      }
      if (!hint || !start.value || !end.value) { return; }

      var s = new Date(start.value + 'T00:00:00');
      var e = new Date(end.value + 'T00:00:00');
      var diff = Math.round((e - s) / 86400000);
      if (unit === '小时') {
        var hours = Math.max(0, Math.round((e - s) / 3600000));
        hint.textContent = '共 ' + hours + ' 小时';
      } else {
        hint.textContent = '共 ' + Math.max(0, diff) + ' 晚';
      }
    }

    start.addEventListener('change', sync);
    if (end) { end.addEventListener('change', sync); }
    sync();
  }

  // ---------------- 6. 首页快捷购票 ----------------
  function initQuickSearch() {
    var form = $('#quickSearchForm');
    if (!form) { return; }
    form.addEventListener('submit', function (evt) {
      evt.preventDefault();
      var movie = $('#qsMovie');
      var date = $('#qsDate');
      if (!movie || !movie.value) { return; }
      var url = '/movies/' + encodeURIComponent(movie.value);
      if (date && date.value) {
        url += '?date=' + encodeURIComponent(date.value);
      }
      window.location.href = url;
    });
  }

  // ---------------- 7. 支付倒计时 ----------------
  // 元素：<span id="payCountdown" data-deadline="2026-11-03 22:30:00">
  // 后端以数据库时间为准，这里只做展示；超时后刷新页面由后端判定"已超时"
  function initCountdown() {
    var el = $('#payCountdown');
    if (!el) { return; }
    var deadlineStr = el.getAttribute('data-deadline');
    if (!deadlineStr) { return; }

    var deadline = new Date(deadlineStr.replace(' ', 'T'));
    if (isNaN(deadline.getTime())) { return; }

    var box = el.closest('.countdown-box') || el;
    var tick = function () {
      var left = Math.floor((deadline.getTime() - Date.now()) / 1000);
      if (left <= 0) {
        el.textContent = '00:00';
        el.classList.add('urgent');
        if (box) { box.classList.add('expired'); }
        var note = $('#countdownNote');
        if (note) { note.textContent = '支付时间已到，订单已自动取消，请重新选座。'; }
        clearInterval(timer);
        return;
      }
      var m = Math.floor(left / 60);
      var s = left % 60;
      el.textContent = (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
      el.classList.toggle('urgent', left <= 60);
    };
    var timer = setInterval(tick, 1000);
    tick();
  }

  // ---------------- 8. 选座 / 实时报价（第 4 步填充） ----------------
  // 本步只提供空实现：页面上没有对应元素时直接返回，不会报错。
  function initSeatMap() {
    var map = $('#seatMap');
    if (!map) { return; }
    // 第 4 步实现：点击座位 -> 加入 / 移出已选、更新底部结算栏、调用 /api/quote 实时报价
  }

  function initQuote() {
    var box = $('#quoteBox');
    if (!box) { return; }
    // 第 4 步实现：选座变化后 fetch('/api/quote') 拉取后端逐座报价
  }

  // ---------------- 启动 ----------------
  document.addEventListener('DOMContentLoaded', function () {
    initNavbar();
    initFlash();
    initFormGuard();
    initConfirm();
    initDateRange();
    initQuickSearch();
    initCountdown();
    initSeatMap();
    initQuote();
  });
})();
