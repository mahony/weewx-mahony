## Camera snapshot widget
#errorCatcher Echo
#encoding UTF-8
#import os
#import time
#import json

#set $cam_img_path = "/var/www/html/weewx/cam_snapshot.jpg"
#set $cam_mtime = 0
#set $cam_age_min = None
#set $cam_mtime_str = ""
#if os.path.isfile($cam_img_path)
  #set $cam_mtime = int(os.path.getmtime($cam_img_path))
  #set $cam_age_min = int((time.time() - $cam_mtime) / 60)
  #set $cam_mtime_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime($cam_mtime))
#end if

#set $cam_snapshots_dir = "/var/www/html/weewx/cam_snapshots"
#set $cam_cutoff = time.time() - 24 * 60 * 60
#set $cam_frame_epochs = sorted([int(fn[:-4]) for fn in os.listdir($cam_snapshots_dir) if fn.endswith(".jpg") and fn[:-4].isdigit() and int(fn[:-4]) >= $cam_cutoff]) if os.path.isdir($cam_snapshots_dir) else []
#set $cam_frames = ["cam_snapshots/%d.jpg" % e for e in $cam_frame_epochs]

<div id='cam_widget' class="widget">
  <div class="widget_title">
    Armory Cam
    <a class="widget_control"
      onclick="toggle_widget('cam')">&diams;</a>
  </div>

  <div class="widget_contents">
    #if $cam_mtime
    <img id="cam_photo" src="cam_snapshot.jpg?t=$cam_mtime" alt="Camera snapshot"
         style="width:100%;height:auto;display:block;cursor:pointer" onclick="camToggle()" />
    <p class="footnote" id="cam_caption" style="text-align:center">
      click photo for 24h timelapse
    </p>
    <p class="footnote" style="text-align:center">
      <a href="cam_archive.html">View archived days &amp; timelapses</a>
    </p>
    <script>
    (function() {
      var camFrames = $json.dumps($cam_frames);
      var camLatestSrc = "cam_snapshot.jpg?t=$cam_mtime";
      var camLatestEpoch = $cam_mtime;
      // Browsable list: archived 24h frames, plus the latest snapshot if it is newer than the last frame.
      var camList = camFrames.map(function(src) {
        return {src: src, epoch: parseInt(src.split('/').pop(), 10)};
      });
      if (!camList.length || camList[camList.length - 1].epoch < camLatestEpoch) {
        camList.push({src: camLatestSrc, epoch: camLatestEpoch});
      }
      var camCaptionHtml = document.getElementById('cam_caption').innerHTML;
      var camTimer = null;
      var camIdx = camList.length - 1;   // index of displayed frame; last = latest snapshot
      var camBrowsed = false;            // true once the user has frozen/stepped away from the default view

      function camShow(i) {
        camIdx = i;
        document.getElementById('cam_photo').src = camList[i].src;
      }

      function camCaptionFor(i) {
        return new Date(camList[i].epoch * 1000).toLocaleString() +
               ' (' + (i + 1) + '/' + camList.length + ')<br/>' +
               '&larr; &rarr; to browse, click photo to play';
      }

      window.camToggle = function() {
        var caption = document.getElementById('cam_caption');
        if (camTimer) {
          // Freeze on the currently displayed frame.
          clearInterval(camTimer);
          camTimer = null;
          camBrowsed = true;
          caption.innerHTML = camCaptionFor(camIdx);
          return;
        }
        if (camList.length < 2) {
          return;
        }
        // Resume from the frozen frame; from the default (latest) view start at the beginning.
        if (!camBrowsed) {
          camShow(0);
        }
        caption.innerHTML = 'Playing 24h timelapse<br/>click photo to freeze';
        camTimer = setInterval(function() {
          camShow((camIdx + 1) % camList.length);
        }, 150);
      };

      function step(delta) {
        if (camTimer) {
          return;
        }
        var next = camIdx + delta;
        if (next >= camList.length) {
          next = 0;   // forward from the current snapshot loops to 24h ago
        }
        if (next < 0) {
          next = camList.length - 1;   // back from the oldest frame loops to the current snapshot
        }
        camBrowsed = true;
        camShow(next);
        document.getElementById('cam_caption').innerHTML = camCaptionFor(next);
      }

      document.addEventListener('keydown', function(e) {
        if (e.key !== 'Escape') {
          return;
        }
        // Return to the current (latest) snapshot, whether playing or browsing.
        if (camTimer) {
          clearInterval(camTimer);
          camTimer = null;
        }
        camBrowsed = false;
        camShow(camList.length - 1);
        document.getElementById('cam_caption').innerHTML = camCaptionHtml;
      });

      document.addEventListener('keydown', function(e) {
        if (camTimer || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) {
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
        window.camToggle();
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
      var swipeImg = document.getElementById('cam_photo');
      swipeImg.style.touchAction = 'pan-y';
      swipeImg.addEventListener('touchstart', function(e) {
        swipeStop();
        if (e.touches.length === 1) {
          swipeX = e.touches[0].clientX;
          swipeY = e.touches[0].clientY;
        }
      }, {passive: true});
      swipeImg.addEventListener('touchmove', function(e) {
        if (swipeX === null || swipeDir || camTimer) { return; }
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
    })();
    </script>
    #else
    <p class="footnote">No snapshot available yet</p>
    #end if
  </div>
</div>
