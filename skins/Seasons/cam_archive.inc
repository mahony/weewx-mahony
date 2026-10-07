## Camera archive browsing widget
#errorCatcher Echo
#encoding UTF-8

<div id='cam_archive_widget' class="widget">
  <div class="widget_title">
    Camera Archive
    <a class="widget_control"
      onclick="toggle_widget('cam_archive')">&diams;</a>
  </div>

  <div class="widget_contents">
    <p class="footnote">
      <label for="cam_archive_date">Date:</label>
      <select id="cam_archive_date"></select>
    </p>
    <img id="cam_archive_photo" alt="Archived camera snapshot"
         style="width:100%;height:auto;display:block;cursor:pointer" onclick="camArchiveToggle()" />
    <p class="footnote" id="cam_archive_caption" style="text-align:center">
      Loading&hellip;
    </p>
    <script>
    (function() {
      var frames = [];
      var timer = null;
      var idx = 0;

      function setStatic(src, caption) {
        document.getElementById('cam_archive_photo').src = src;
        document.getElementById('cam_archive_caption').innerHTML = caption;
      }

      // dir: 0 = user picked the day (show its first frame); 1 / -1 = arrived by stepping
      // forward / backward from a neighbouring day (land on its first / last frame).
      function loadDate(date, dir) {
        dir = dir || 0;
        if (timer) {
          clearInterval(timer);
          timer = null;
        }
        frames = [];
        idx = 0;
        if (dir) {
          // Keep the current photo on screen while the neighbouring day loads.
          document.getElementById('cam_archive_caption').innerHTML = 'Loading ' + date + '&hellip;';
        } else {
          setStatic('', 'Loading&hellip;');
        }
        fetch('cam_archive/' + date + '/frames.json', {cache: 'no-store'})
          .then(function(resp) { return resp.json(); })
          .then(function(data) {
            frames = (data.epochs || []).map(function(e) {
              return 'cam_archive/' + date + '/' + e + '.jpg';
            });
            if (!frames.length) {
              if (dir) {
                stepDay(dir);   // skip an empty day
              } else {
                setStatic('', 'No frames archived for ' + date);
              }
            } else if (dir) {
              showFrame(dir > 0 ? 0 : frames.length - 1, date);
            } else {
              setStatic(frames[0], frames.length + ' frames on ' + date + '<br/>click photo to play');
            }
          })
          .catch(function() {
            setStatic('', 'Could not load frames for ' + date);
          });
      }

      // Move to the next (dir = 1, newer) or previous (dir = -1, older) archived day,
      // wrapping around at the ends of the archive. The date list is newest first.
      function stepDay(dir) {
        var select = document.getElementById('cam_archive_date');
        var n = select.options.length;
        if (n < 1) {
          return;
        }
        select.selectedIndex = ((select.selectedIndex - dir) % n + n) % n;
        loadDate(select.value, dir);
      }

      function frameCaption(date) {
        return new Date(parseInt(frames[idx].split('/').pop(), 10) * 1000).toLocaleString() +
               ' (' + (idx + 1) + '/' + frames.length + ')<br/>' +
               '&larr; &rarr; to browse, click photo to play';
      }

      function showFrame(i, date) {
        idx = i;
        document.getElementById('cam_archive_photo').src = frames[idx];
        document.getElementById('cam_archive_caption').innerHTML = frameCaption(date);
      }

      window.camArchiveToggle = function() {
        var date = document.getElementById('cam_archive_date').value;
        if (timer) {
          // Freeze on the currently displayed frame.
          clearInterval(timer);
          timer = null;
          document.getElementById('cam_archive_caption').innerHTML = frameCaption(date);
          return;
        }
        if (!frames.length) {
          return;
        }
        document.getElementById('cam_archive_caption').innerHTML = 'Playing timelapse<br/>click photo to freeze';
        timer = setInterval(function() {
          idx = (idx + 1) % frames.length;
          document.getElementById('cam_archive_photo').src = frames[idx];
        }, 150);
      };

      document.addEventListener('keydown', function(e) {
        if (e.key !== 'Escape' || !frames.length) {
          return;
        }
        // Return to the day's first frame, whether playing or browsing.
        if (timer) {
          clearInterval(timer);
          timer = null;
        }
        idx = 0;
        setStatic(frames[0], frames.length + ' frames on ' + document.getElementById('cam_archive_date').value + '<br/>click photo to play');
      });

      function step(delta) {
        var next = idx + delta;
        if (timer || !frames.length) {
          return;
        }
        if (next < 0 || next >= frames.length) {
          stepDay(delta);
          return;
        }
        showFrame(next, document.getElementById('cam_archive_date').value);
      }

      document.addEventListener('keydown', function(e) {
        if (timer || !frames.length || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) {
          return;
        }
        var t = e.target && e.target.tagName;
        if (t === 'INPUT' || t === 'SELECT' || t === 'TEXTAREA' || (e.target && e.target.isContentEditable)) {
          return;
        }
        if (e.key === 'ArrowLeft') {
          step(-1);
        } else if (e.key === 'ArrowRight') {
          step(1);
        } else {
          return;
        }
        e.preventDefault();
      });

      document.addEventListener('keydown', function(e) {
        if (e.key !== ' ' || e.repeat || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) {
          return;
        }
        var t = e.target && e.target.tagName;
        if (t === 'INPUT' || t === 'SELECT' || t === 'TEXTAREA' || t === 'BUTTON' || t === 'A' || (e.target && e.target.isContentEditable)) {
          return;
        }
        e.preventDefault();   // don't scroll the page
        window.camArchiveToggle();
      });

      // Touch: swipe left = right arrow (next), swipe right = left arrow (previous).
      // Holding after the swipe auto-repeats like a held key (500ms delay, then ~30/s).
      var swipeX = null, swipeY = null, swipeDir = 0, repeatDelay = null, repeatTimer = null;
      function swipeStop() {
        swipeX = null;
        swipeDir = 0;
        clearTimeout(repeatDelay);
        clearInterval(repeatTimer);
      }
      var swipeImg = document.getElementById('cam_archive_photo');
      swipeImg.style.touchAction = 'pan-y';
      swipeImg.addEventListener('touchstart', function(e) {
        swipeStop();
        if (e.touches.length === 1) {
          swipeX = e.touches[0].clientX;
          swipeY = e.touches[0].clientY;
        }
      }, {passive: true});
      swipeImg.addEventListener('touchmove', function(e) {
        if (swipeX === null || swipeDir || timer) { return; }
        var dx = e.touches[0].clientX - swipeX;
        var dy = e.touches[0].clientY - swipeY;
        if (Math.abs(dx) >= 40 && Math.abs(dx) > 1.5 * Math.abs(dy)) {
          swipeDir = dx < 0 ? 1 : -1;
          step(swipeDir);
          repeatDelay = setTimeout(function() {
            repeatTimer = setInterval(function() { step(swipeDir); }, 33);
          }, 500);
        }
      }, {passive: true});
      swipeImg.addEventListener('touchend', swipeStop);
      swipeImg.addEventListener('touchcancel', swipeStop);

      var select = document.getElementById('cam_archive_date');
      select.addEventListener('change', function() {
        loadDate(select.value);
      });

      fetch('cam_archive/index.json', {cache: 'no-store'})
        .then(function(resp) { return resp.json(); })
        .then(function(data) {
          var dates = data.dates || [];
          if (!dates.length) {
            setStatic('', 'No archived days yet');
            return;
          }
          dates.forEach(function(date) {
            var opt = document.createElement('option');
            opt.value = date;
            opt.textContent = date;
            select.appendChild(opt);
          });
          loadDate(dates[0]);
        })
        .catch(function() {
          setStatic('', 'Could not load archive index');
        });
    })();
    </script>
  </div>
</div>
