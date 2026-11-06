// static/js/admin.js —— 后台交互（原生 JS + Bootstrap 5 自带模态框，无第三方库）
/**
 * 本文件包含：
 * 1. flash 提示 4 秒自动消失
 * 2. 表单提交防重复（禁用按钮 + "正在提交…"）
 * 3. 带 data-confirm 的按钮 / 表单二次确认
 * 4. 统一的 JSON 请求封装（自动带 X-CSRF-Token，401 提示重新登录）
 * 5. 排片时间轴：点空白新建、点场次块编辑 / 停售 / 取消（fetch 调 /admin/api/...）
 * 6. 座位编辑器：画笔切换类型、标记改动、保存
 * 7. 影片状态切换、影厅启用停用
 * 8. 订单检票 / 退票、按取票码检票
 *
 * 约定：所有写请求都带 CSRF 令牌（<meta name="csrf-token">）；结果一律用 textContent 渲染。
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

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
  }

  /** 统一的 POST JSON 封装：返回 {ok, message, ...}，网络错误也转成中文提示 */
  function api(url, data) {
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
        if (resp.status === 401) {
          body.ok = false;
          body.message = body.message || '登录已过期，请重新登录';
        }
        if (!resp.ok && !body.message) {
          body.ok = false;
          body.message = '请求失败（' + resp.status + '）';
        }
        return body;
      });
    }).catch(function () {
      return { ok: false, message: '网络异常，请检查连接后重试' };
    });
  }

  /** 浮动提示：复用 flash 的样式，4 秒后自动消失 */
  function toast(message, ok) {
    var wrap = document.createElement('div');
    wrap.className = 'flash-wrap';

    var item = document.createElement('div');
    item.className = 'flash-item flash-' + (ok ? 'success' : 'error');
    item.setAttribute('role', 'alert');

    var icon = document.createElement('i');
    icon.className = 'bi ' + (ok ? 'bi-check-circle-fill' : 'bi-exclamation-octagon-fill');
    var text = document.createElement('span');
    text.textContent = message;

    item.appendChild(icon);
    item.appendChild(text);
    wrap.appendChild(item);
    document.body.appendChild(wrap);

    setTimeout(function () {
      wrap.style.transition = 'opacity .35s ease';
      wrap.style.opacity = '0';
      setTimeout(function () {
        if (wrap.parentNode) { wrap.parentNode.removeChild(wrap); }
      }, 380);
    }, 4000);
  }

  /** 分钟数 -> 'HH:MM' */
  function toHHMM(minutes) {
    var h = Math.floor(minutes / 60);
    var m = minutes % 60;
    return (h < 10 ? '0' : '') + h + ':' + (m < 10 ? '0' : '') + m;
  }

  // ---------------- 1. flash 自动消失 ----------------
  function initFlash() {
    var items = $all('.flash-item');
    if (!items.length) { return; }
    setTimeout(function () {
      items.forEach(function (item) {
        item.style.transition = 'opacity .35s ease, transform .35s ease';
        item.style.opacity = '0';
        item.style.transform = 'translateX(20px)';
      });
      setTimeout(function () {
        items.forEach(function (item) {
          if (item.parentNode) { item.parentNode.removeChild(item); }
        });
      }, 380);
    }, 4000);
  }

  // ---------------- 2. 表单提交防重复 ----------------
  function initFormGuard() {
    $all('form').forEach(function (form) {
      form.addEventListener('submit', function (evt) {
        if (form.dataset.submitting === '1') {
          evt.preventDefault();
          return;
        }
        form.dataset.submitting = '1';
        $all('button[type="submit"]', form).forEach(function (btn) {
          btn.disabled = true;
          if (!btn.dataset.originalText) { btn.dataset.originalText = btn.innerHTML; }
          btn.textContent = '正在提交…';
        });
      });
    });
  }

  // ---------------- 3. 二次确认（捕获阶段，先于其它点击逻辑） ----------------
  function initConfirm() {
    document.addEventListener('click', function (evt) {
      var el = evt.target.closest ? evt.target.closest('[data-confirm]') : null;
      if (!el) { return; }
      if (!window.confirm(el.getAttribute('data-confirm'))) {
        evt.preventDefault();
        evt.stopPropagation();
      }
    }, true);
  }

  // ---------------- 4. 排片时间轴 ----------------
  function initSchedule() {
    var modalEl = document.getElementById('showModal');
    if (!modalEl || !window.bootstrap) { return; }

    var dataEl = document.getElementById('scheduleData');
    if (!dataEl) { return; }
    var cfg = JSON.parse(dataEl.textContent);

    var modal = new bootstrap.Modal(modalEl);
    var titleEl = document.getElementById('showModalTitle');
    var idInput = document.getElementById('showId');
    var hallInput = document.getElementById('showHall');
    var dateInput = document.getElementById('showDate');
    var movieSel = document.getElementById('showMovie');
    var startInput = document.getElementById('showStart');
    var priceInput = document.getElementById('showPrice');
    var endHint = document.getElementById('showEndHint');
    var infoEl = document.getElementById('showInfo');
    var btnSave = document.getElementById('btnSaveShow');
    var btnStop = document.getElementById('btnStopShow');
    var btnCancel = document.getElementById('btnCancelShow');

    var mode = 'create';
    var showId = null;
    var showStatus = 'ON_SALE';
    var sold = 0;

    function selectedDuration() {
      var opt = movieSel.options[movieSel.selectedIndex];
      var d = opt ? parseInt(opt.getAttribute('data-duration'), 10) : 0;
      return isNaN(d) ? 0 : d;
    }

    function updateEndHint() {
      var duration = selectedDuration();
      var value = startInput.value;
      if (!value || !duration) {
        endHint.textContent = '选择影片与开始时间后显示结束时间（含 ' + cfg.clean + ' 分钟清场）。';
        return;
      }
      var parts = value.split(':');
      var startMin = parseInt(parts[0], 10) * 60 + parseInt(parts[1], 10);
      var endMin = startMin + duration + cfg.clean;
      if (endMin > 24 * 60) {
        endHint.textContent = '结束时间会超过 24:00（片长 ' + duration + ' 分钟 + ' +
          cfg.clean + ' 分钟清场），请选择更早的开始时间。';
        return;
      }
      endHint.textContent = '结束时间（含 ' + cfg.clean + ' 分钟清场）约 ' + toHHMM(endMin) +
        '，保存时后端会再次校验同厅不重叠。';
    }

    function resetInfo() {
      infoEl.style.display = 'none';
      infoEl.textContent = '';
      infoEl.className = 'notice-line';
    }

    function openCreate(hallId, startTime) {
      mode = 'create';
      showId = null;
      showStatus = 'ON_SALE';
      sold = 0;
      titleEl.textContent = '新建场次';
      idInput.value = '';
      hallInput.value = hallId;
      dateInput.value = cfg.date;
      startInput.value = startTime || '10:00';
      priceInput.value = 50;
      if (cfg.movies.length) { movieSel.selectedIndex = 0; }

      btnSave.style.display = '';
      btnStop.style.display = 'none';
      btnCancel.style.display = 'none';
      resetInfo();
      updateEndHint();
      modal.show();
    }

    function openEdit(btn) {
      mode = 'edit';
      showId = btn.getAttribute('data-show-id');
      showStatus = btn.getAttribute('data-status');
      sold = parseInt(btn.getAttribute('data-sold'), 10) || 0;
      var title = btn.getAttribute('data-title');
      var start = btn.getAttribute('data-start');

      titleEl.textContent = '编辑场次';
      idInput.value = showId;
      hallInput.value = btn.closest('.tl-track').getAttribute('data-hall-id');
      dateInput.value = cfg.date;
      startInput.value = start;
      priceInput.value = parseFloat(btn.getAttribute('data-price')) || 0;

      // 影片下拉按片名匹配（排片块里没有 index，片名唯一）
      var matched = false;
      $all('option', movieSel).forEach(function (opt) {
        if (!matched && opt.textContent.trim().indexOf(title) === 0) {
          opt.selected = true;
          matched = true;
        }
      });

      if (matched) {
        btnSave.style.display = '';
        btnSave.disabled = false;
        infoEl.style.display = 'flex';
        infoEl.className = 'notice-line';
        infoEl.textContent = '已售 ' + sold + ' / ' + btn.getAttribute('data-capacity') +
          ' 座（' + btn.getAttribute('data-pct') + '%）· 当前状态：' +
          (showStatus === 'ON_SALE' ? '在售' : '停售') +
          (sold > 0 ? ' · 已有售出票，只能改票价，不能改影片或时间' : '');
      } else {
        // 影片已下映（不在候选列表里），只能停售 / 取消
        btnSave.style.display = 'none';
        infoEl.style.display = 'flex';
        infoEl.className = 'notice-line';
        infoEl.textContent = '该场次的影片已不在可排片状态，只能停售或取消本场。';
      }

      btnStop.style.display = '';
      btnStop.innerHTML = showStatus === 'STOPPED'
        ? '<i class="bi bi-play-circle"></i> 恢复售票'
        : '<i class="bi bi-pause-circle"></i> 停售';
      // 已有售出票的场次只能停售，不能删除
      btnCancel.style.display = sold > 0 ? 'none' : '';

      updateEndHint();
      modal.show();
    }

    // 点场次块 -> 编辑
    $all('.show-block').forEach(function (btn) {
      btn.addEventListener('click', function (evt) {
        evt.stopPropagation();
        openEdit(btn);
      });
    });

    // 点时间轴空白 -> 新建，按点击位置换算开始时间（对齐 5 分钟）
    $all('.tl-track').forEach(function (track) {
      track.addEventListener('click', function (evt) {
        var rect = track.getBoundingClientRect();
        var ratio = (evt.clientX - rect.left) / rect.width;
        if (ratio < 0) { ratio = 0; }
        if (ratio > 1) { ratio = 1; }
        var minutes = cfg.startMin + ratio * cfg.span;
        minutes = Math.round(minutes / 5) * 5;
        if (minutes < cfg.startMin) { minutes = cfg.startMin; }
        if (minutes > 23 * 60 + 55) { minutes = 23 * 60 + 55; }
        openCreate(track.getAttribute('data-hall-id'), toHHMM(minutes));
      });
    });

    movieSel.addEventListener('change', updateEndHint);
    startInput.addEventListener('change', updateEndHint);

    // 保存（新建 / 编辑）
    btnSave.addEventListener('click', function () {
      var payload = {
        movie_id: movieSel.value,
        start_time: startInput.value,
        price: priceInput.value
      };
      var url;
      if (mode === 'create') {
        payload.hall_id = hallInput.value;
        payload.show_date = dateInput.value;
        url = '/admin/api/schedule/create';
      } else {
        url = '/admin/api/schedule/' + showId + '/update';
      }

      btnSave.disabled = true;
      api(url, payload).then(function (res) {
        toast(res.message, res.ok);
        if (res.ok) {
          setTimeout(function () { window.location.reload(); }, 700);
        } else {
          btnSave.disabled = false;
        }
      });
    });

    // 停售 / 恢复售票
    btnStop.addEventListener('click', function () {
      var action = showStatus === 'STOPPED' ? 'resume' : 'stop';
      btnStop.disabled = true;
      api('/admin/api/schedule/' + showId + '/status', { action: action }).then(function (res) {
        toast(res.message, res.ok);
        if (res.ok) {
          setTimeout(function () { window.location.reload(); }, 700);
        } else {
          btnStop.disabled = false;
        }
      });
    });

    // 取消场次（已有售出票时后端也会拒绝）
    btnCancel.addEventListener('click', function () {
      if (!window.confirm('取消后该场次不再售票，确定取消吗？')) { return; }
      btnCancel.disabled = true;
      api('/admin/api/schedule/' + showId + '/status', { action: 'cancel' }).then(function (res) {
        toast(res.message, res.ok);
        if (res.ok) {
          setTimeout(function () { window.location.reload(); }, 700);
        } else {
          btnCancel.disabled = false;
        }
      });
    });
  }

  // ---------------- 5. 座位编辑器 ----------------
  function initSeatEditor() {
    var grid = document.getElementById('seatEditor');
    if (!grid) { return; }

    var labels = { NORMAL: '普', COUPLE: '情', VIP: 'V', NONE: '空' };
    var brush = 'NORMAL';
    var counter = document.getElementById('seatChanges');

    function changedCells() {
      return $all('.seat-cell', grid).filter(function (cell) {
        return cell.getAttribute('data-type') !== cell.getAttribute('data-orig');
      });
    }

    function refreshCount() {
      var n = changedCells().length;
      counter.textContent = n ? '有 ' + n + ' 个座位待保存' : '未修改';
    }

    $all('[data-brush]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        brush = btn.getAttribute('data-brush');
        $all('[data-brush]').forEach(function (b) { b.classList.remove('active'); });
        btn.classList.add('active');
      });
    });

    grid.addEventListener('click', function (evt) {
      var cell = evt.target.closest('.seat-cell');
      if (!cell || !cell.getAttribute('data-seat-id')) { return; }
      cell.setAttribute('data-type', brush);
      cell.className = 'seat-cell t-' + brush;
      cell.textContent = labels[brush] || '空';
      refreshCount();
    });

    var btnReset = document.getElementById('btnResetSeats');
    if (btnReset) {
      btnReset.addEventListener('click', function () {
        if (changedCells().length && !window.confirm('放弃所有未保存的修改吗？')) { return; }
        window.location.reload();
      });
    }

    var btnSave = document.getElementById('btnSaveSeats');
    if (!btnSave) { return; }
    btnSave.addEventListener('click', function () {
      var cells = changedCells();
      if (!cells.length) {
        toast('没有需要保存的修改', true);
        return;
      }
      var changes = {};
      cells.forEach(function (cell) {
        changes[cell.getAttribute('data-seat-id')] = cell.getAttribute('data-type');
      });

      btnSave.disabled = true;
      api('/admin/api/halls/' + grid.getAttribute('data-hall-id') + '/seats', { changes: changes })
        .then(function (res) {
          toast(res.message, res.ok);
          if (res.ok) {
            setTimeout(function () { window.location.reload(); }, 900);
          } else {
            btnSave.disabled = false;
          }
        });
    });
  }

  // ---------------- 6. 影片状态 / 影厅启用停用 ----------------
  function initMovieStatus() {
    $all('[data-movie-status]').forEach(function (sel) {
      sel.addEventListener('change', function () {
        var id = sel.getAttribute('data-movie-status');
        sel.disabled = true;
        api('/admin/api/movies/' + id + '/status', { status: sel.value }).then(function (res) {
          toast(res.message, res.ok);
          sel.disabled = false;
          if (!res.ok) { window.location.reload(); }
        });
      });
    });
  }

  function initHallToggle() {
    $all('[data-hall-toggle]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var id = btn.getAttribute('data-hall-toggle');
        var enabled = btn.getAttribute('data-enabled') === '1';
        btn.disabled = true;
        api('/admin/api/halls/' + id + '/toggle', { is_enabled: enabled }).then(function (res) {
          toast(res.message, res.ok);
          if (res.ok) {
            setTimeout(function () { window.location.reload(); }, 700);
          } else {
            btn.disabled = false;
          }
        });
      });
    });
  }

  // ---------------- 7. 订单检票 / 退票 / 按码检票 ----------------
  function initOrderActions() {
    $all('[data-order-action]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var action = btn.getAttribute('data-order-action');
        var no = btn.getAttribute('data-order-no');
        btn.disabled = true;
        api('/admin/api/orders/' + encodeURIComponent(no) + '/' + action, {}).then(function (res) {
          toast(res.message, res.ok);
          if (res.ok) {
            setTimeout(function () { window.location.reload(); }, 800);
          } else {
            btn.disabled = false;
          }
        });
      });
    });

    $all('[data-checkin-code]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        btn.disabled = true;
        api('/admin/api/checkin', { code: btn.getAttribute('data-checkin-code') }).then(function (res) {
          toast(res.message, res.ok);
          if (res.ok) {
            setTimeout(function () { window.location.reload(); }, 900);
          } else {
            btn.disabled = false;
          }
        });
      });
    });
  }

  // ---------------- 启动 ----------------
  document.addEventListener('DOMContentLoaded', function () {
    initFlash();
    initFormGuard();
    initConfirm();
    initSchedule();
    initSeatEditor();
    initMovieStatus();
    initHallToggle();
    initOrderActions();
  });
})();
