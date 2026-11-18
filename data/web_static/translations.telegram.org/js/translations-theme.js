var TrTheme = {
  SWITCHING_CLASS: 'tr-theme-switching',
  isDark: function() {
    return document.documentElement.getAttribute('data-theme') === 'dark';
  },
  apply: function(theme) {
    var root = document.documentElement;
    var dark = theme === 'dark';
    if (TrTheme.isDark() === dark) {
      root.setAttribute('data-theme', dark ? 'dark' : 'light');
      TrTheme.updateButtons();
      return;
    }
    root.classList.add(TrTheme.SWITCHING_CLASS);
    root.offsetHeight;
    root.setAttribute('data-theme', dark ? 'dark' : 'light');
    document.dispatchEvent(new Event('darkmode'));
    if (window.Telegram && Telegram.setWidgetOptions) {
      Telegram.setWidgetOptions({dark: dark});
    }
    TrTheme.updateButtons();
    if (window.requestAnimationFrame) {
      window.requestAnimationFrame(function() {
        root.classList.remove(TrTheme.SWITCHING_CLASS);
      });
    } else {
      window.setTimeout(function() {
        root.classList.remove(TrTheme.SWITCHING_CLASS);
      }, 0);
    }
  },

  applyNoFreeze: function(theme) {
    var root = document.documentElement;
    var dark = theme === 'dark';
    if (TrTheme.isDark() === dark) {
      root.setAttribute('data-theme', dark ? 'dark' : 'light');
      TrTheme.updateButtons();
      return;
    }
    root.setAttribute('data-theme', dark ? 'dark' : 'light');
    document.dispatchEvent(new Event('darkmode'));
    if (window.Telegram && Telegram.setWidgetOptions) {
      Telegram.setWidgetOptions({dark: dark});
    }
    TrTheme.updateButtons();
  },
  commit: function(theme) {
    document.cookie = Aj.state.themeCookie + '=' + theme + ';path=/;max-age=31536000';
    TrTheme.apply(theme);
  },
  updateButtons: function() {
    var dark = TrTheme.isDark();
    $('.tr-theme-toggle').attr({
      'aria-pressed': dark ? 'true' : 'false',
      'aria-label': dark ? 'Switch to light theme' : 'Switch to dark theme',
      'title': dark ? 'Light theme' : 'Dark theme'
    });
  }
};

var TrThemeToggle = (function () {
  var W = 44, H = 44; // render at 2x of the 22px display size
  var SUN_FRAME = 0;  // frame 0 = sun (light)
  var MOON_FRAME = 0; // last frame = moon (dark); set after load

  var WIPE_DARK_MS = 800;
  var WIPE_LIGHT_MS = 500;
  var generation = 0;

  var btn = null;
  var canvas = null, ctx = null;
  var player = null, ready = false, cur = 0, raf = 0, animTarget = null;
  var rawCache = {};
  var replaced = false;
  var themeState = 'light';
  var activeVT = null;
  var ownedAnimations = [];

  function cancelOwnedAnimation(owner) {
    if (!owner) return;
    try { owner.animation.cancel(); } catch (e) {}
    var index = ownedAnimations.indexOf(owner);
    if (index !== -1) ownedAnimations.splice(index, 1);
  }

  function cancelAllOwnedAnimations() {
    ownedAnimations.slice().forEach(function (owner) {
      cancelOwnedAnimation(owner);
    });
  }

  function animatedClick() {
    doToggle(
      function () { snapIcon(); },
      function (reduce) {
        if (reduce) snapIcon(); else morphIcon();
      }
    );
  }

  function init() {
    btn = document.querySelector('.tr-theme-toggle');
    if (!btn) return;
    if (btn.querySelector('canvas')) return;

    themeState = TrTheme.isDark() ? 'dark' : 'light';
    /* Ajax replaces the page content; keep the decoded player and move its canvas. */
    if (canvas) {
      btn.appendChild(canvas);
      btn.addEventListener('click', animatedClick);
      if (ready) {
        snapIcon();
        btn.classList.remove('tr-theme-toggle--static');
        replaced = true;
      }
      return;
    }

    generation++;
    ready = false;
    cur = 0;
    animTarget = null;
    rawCache = {};
    replaced = false;

    if (typeof RLottie === 'undefined' || !RLottie.isSupported) {
      btn.addEventListener('click', function () { doToggle(null, null); });
      return;
    }

    /* ---------- Animated lottie path ---------- */
    canvas = document.createElement('canvas');
    canvas.width = W; canvas.height = H;
    btn.appendChild(canvas);
    ctx = canvas.getContext('2d');

    btn.addEventListener('click', animatedClick);

    var gen = generation;
    RLottie.WORKERS_LIMIT = 1;
    RLottie.initWorkers(function () {
      player = RLottie.createRawPlayer(W, H, function (frameCount, fps) {
        if (!btn.isConnected || gen !== generation) return;
        MOON_FRAME = frameCount - 1;
        ready = true;
        cur = themeState === 'dark' ? MOON_FRAME : SUN_FRAME;
        player.renderFrame(cur);
        var other = themeState === 'dark' ? SUN_FRAME : MOON_FRAME;
        player.renderFrame(other);
        if (!replaced) {
          btn.classList.remove('tr-theme-toggle--static');
          replaced = true;
        }
      }, function (frameNo, pixels) {
        if (gen !== generation) return;
        onFrame(frameNo, pixels);
      });
      player.loadFromUrl('/img/sun_outline.tgs');
    });
  }

  /* Use the largest viewport for the snapshot. */
  function getSnapshotViewportSize() {
    var root = document.documentElement;
    var width = root.clientWidth || innerWidth || 1;
    var height = root.clientHeight || innerHeight || 1;

    if (window.CSS && CSS.supports &&
        CSS.supports('width', '100lvw') && CSS.supports('height', '100lvh')) {
      var probe = document.createElement('div');
      probe.style.cssText = 'position:fixed;left:0;top:0;width:100lvw;height:100lvh;' +
        'visibility:hidden;pointer-events:none;';
      root.appendChild(probe);
      var rect = probe.getBoundingClientRect();
      root.removeChild(probe);
      if (isFinite(rect.width) && rect.width > 0) width = Math.max(width, rect.width);
      if (isFinite(rect.height) && rect.height > 0) height = Math.max(height, rect.height);
    }

    return { width: width, height: height };
  }

  /* Align Y with the snapshot's containing block. */
  function getSnapshotButtonCenterY(fallbackY) {
    var pseudo = '::view-transition-group(tr-theme-toggle)';
    var Matrix = window.DOMMatrixReadOnly || window.DOMMatrix || window.WebKitCSSMatrix;
    if (!Matrix) return fallbackY;
    var animations = document.getAnimations ? document.getAnimations() : [];
    for (var i = 0; i < animations.length; i++) {
      var effect = animations[i].effect;
      if (!effect || effect.pseudoElement !== pseudo || !effect.getKeyframes) continue;
      var keyframes = effect.getKeyframes();
      for (var j = 0; j < keyframes.length; j++) {
        var offset = keyframes[j].computedOffset;
        if (offset === undefined) offset = keyframes[j].offset;
        if (offset !== 0) continue;
        var height = parseFloat(keyframes[j].height);
        var transform = keyframes[j].transform;
        if (!isFinite(height) || height <= 0 || !transform) continue;
        try {
          var matrix = new Matrix(transform);
          var centerY = matrix.f + height / 2;
          if (isFinite(centerY)) return centerY;
        } catch (e) {}
      }
    }
    /* Fall back to the current group geometry. */
    try {
      var style = getComputedStyle(document.documentElement, pseudo);
      var styleHeight = parseFloat(style.height);
      var styleMatrix = new Matrix(style.transform);
      var styleCenterY = styleMatrix.f + styleHeight / 2;
      if (isFinite(styleHeight) && styleHeight > 0 && isFinite(styleCenterY)) {
        return styleCenterY;
      }
    } catch (e) {}
    return fallbackY;
  }

  function doToggle(applyInVT, applyInstant) {
    themeState = themeState === 'dark' ? 'light' : 'dark';
    var t = themeState;
    var reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;

    /* No View Transitions: instant theme change. */
    if (!document.startViewTransition) {
      TrTheme.commit(t);
      if (applyInstant) applyInstant(reduce);
      return;
    }
    /* Reduced motion: snap, no reveal. */
    if (reduce) {
      TrTheme.commit(t);
      if (applyInstant) applyInstant(reduce);
      return;
    }

    if (activeVT && activeVT.skipTransition) {
      try { activeVT.skipTransition(); } catch (e) {}
    }
    cancelAllOwnedAnimations();

    var buttonRect = btn.getBoundingClientRect();
    var animatedSnapshot = '::view-transition-old(root)';

    var root = document.documentElement;
    root.classList.add(TrTheme.SWITCHING_CLASS);
    root.classList.toggle('tr-theme-reveal', t === 'dark');
    root.classList.remove('tr-theme-collapse');
    root.offsetWidth;

    var vt = document.startViewTransition(function () {
      document.cookie = Aj.state.themeCookie + '=' + themeState + ';path=/;max-age=31536000';
      TrTheme.applyNoFreeze(themeState);
      if (applyInVT) applyInVT();
    });
    activeVT = vt;
    var viewAnimationOwner = null;

    vt.ready.then(function () {
      if (activeVT !== vt) {
        return;
      }
      try {
        var viewport = getSnapshotViewportSize();
        var vw = viewport.width;
        var vh = viewport.height;
        var cx = buttonRect.left + buttonRect.width / 2;
        var cy = buttonRect.top + buttonRect.height / 2;
        if (!isFinite(cx)) cx = vw / 2;
        if (!isFinite(cy)) cy = vh / 2;
        cy = getSnapshotButtonCenterY(cy);
        root.style.setProperty('--tr-theme-origin-x', cx + 'px');
        root.style.setProperty('--tr-theme-origin-y', cy + 'px');
        var animationKeyframes;
        if (t === 'dark') {
          animationKeyframes = {
            '--tr-theme-hole-radius': ['0%', '102%']
          };
        } else {
          root.classList.add('tr-theme-collapse');
          animationKeyframes = {
            '--tr-theme-hole-radius': ['102%', '0%']
          };
        }
        root.offsetWidth;
        var anim = root.animate(
          animationKeyframes,
          { duration: t === 'dark' ? WIPE_DARK_MS : WIPE_LIGHT_MS,
            easing: 'cubic-bezier(0.23, 1, 0.32, 1)',
            fill: 'forwards',
            pseudoElement: animatedSnapshot }
        );
        viewAnimationOwner = {
          animation: anim
        };
        ownedAnimations.push(viewAnimationOwner);
      } catch (e) {}
    }, function () {});

    var clear = function () {
      var isCurrent = activeVT === vt;

      if (viewAnimationOwner) {
        cancelOwnedAnimation(viewAnimationOwner);
        viewAnimationOwner = null;
      }

      if (isCurrent) {
        activeVT = null;
        root.classList.remove(TrTheme.SWITCHING_CLASS);
        root.classList.remove('tr-theme-reveal');
        root.classList.remove('tr-theme-collapse');
        root.style.removeProperty('--tr-theme-origin-x');
        root.style.removeProperty('--tr-theme-origin-y');
      }
    };
    vt.finished.then(clear, clear);
  }

  function iconRGB() {
    var m = getComputedStyle(btn).color.match(/(\d+)\D+(\d+)\D+(\d+)/);
    return m ? [+m[1], +m[2], +m[3]] : [136, 136, 136];
  }

  function recolourAndDraw(rawPixels) {
    var arr = new Uint8ClampedArray(rawPixels);
    var c = iconRGB();
    for (var k = 0; k < arr.length; k += 4) {
      arr[k] = c[0]; arr[k + 1] = c[1]; arr[k + 2] = c[2];
    }
    ctx.putImageData(new ImageData(arr, W, H), 0, 0);
  }

  function onFrame(frameNo, pixels) {
    rawCache[frameNo] = pixels;
    if (frameNo === cur) recolourAndDraw(pixels);
    if (animTarget !== null) {
      if (cur === animTarget) {
        animTarget = null;
      } else if (frameNo === cur) {
        raf = requestAnimationFrame(stepAnimation);
      }
    }
  }

  function stepAnimation() {
    raf = 0;
    if (!ready || animTarget === null) return;
    if (cur === animTarget) { animTarget = null; return; }
    var dir = animTarget > cur ? 2 : -2;
    var next = cur + dir;
    if ((dir > 0 && next >= animTarget) || (dir < 0 && next <= animTarget)) next = animTarget;
    cur = next;
    player.renderFrame(next);
  }

  function animateTo(target) {
    if (!ready) { cur = target; return; }
    if (raf) cancelAnimationFrame(raf);
    animTarget = target;
    stepAnimation();
  }

  function morphIcon() {
    animateTo(themeState === 'dark' ? MOON_FRAME : SUN_FRAME);
  }

  function snapIcon() {
    if (raf) cancelAnimationFrame(raf);
    animTarget = null;
    cur = (themeState === 'dark' ? MOON_FRAME : SUN_FRAME);
    var raw = rawCache[cur];
    if (raw) {
      recolourAndDraw(raw);
    } else if (player) {
      player.renderFrame(cur);
    }
  }

  return { init: init };
})();
