"""Single-window SDO client: the live site remains fully interactive inside the app."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import keyring
import webview
from PIL import Image

SERVICE = "SDO-Reborn"
BASE = "https://sdo.i-spo.ru"


def icon_image():
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return Image.open(root / "assets/icon.png").convert("RGBA")


NAV = [
    ("⌂", "Главная", "/"),
    ("▦", "Мои курсы", "/my/courses.php"),
    ("◈", "Учебный план", "/"),
    ("✓", "Задания", "/my/"),
    ("◷", "Календарь", "/calendar/view.php?view=upcoming"),
    ("✉", "Сообщения", "/message/index.php"),
    ("▤", "Почтовый ящик", "/message/index.php"),
    ("♧", "Уведомления", "/"),
    ("▣", "Вебинары", "/?action=bbb_calendar"),
    ("◉", "Новости", "/?action=notice_official"),
    ("▧", "Документы", "/?action=docs"),
    ("▥", "Учебный график", "/?action=kug"),
    ("▤", "Электронные документы студента", "/"),
    ("◈", "Контакты", "/mod/page/view.php?id=38353"),
    ("◉", "Видеоотзыв", "/?action=videoreview"),
    ("◍", "Профиль", "/user/profile.php"),
]


class CredentialBridge:
    def __init__(self, shell):
        self.shell = shell

    def capture_credentials(self, username, password):
        """Remember a manual login only after successful navigation."""
        if isinstance(username, str) and isinstance(password, str) and 0 < len(username) < 256 and 0 < len(password) < 4096:
            self.shell.pending_credentials = (username.strip(), password)


class Shell:
    def __init__(self):
        self.pending_credentials = None
        self.autologin_attempted = False
        self.window = webview.create_window(
            "ОСЭК-Remastered", BASE + "/", width=1440, height=900,
            min_size=(1000, 650), background_color="#101426",
            js_api=CredentialBridge(self),
        )
        self.window.events.loaded += self.loaded
        self.tray = None
        self.quitting = False
        self.window.events.closing += self.closing

    def loaded(self):
        try:
            url = self.window.evaluate_js("location.href")
            if urlparse(url).hostname != "sdo.i-spo.ru":
                return
            if "/login/index.php" in url:
                self.pending_credentials = None
                self.window.evaluate_js("""(function(){
                  const form=document.querySelector('input[name="username"]')?.form;
                  if(!form||form.dataset.osekSave)return;
                  form.dataset.osekSave='1';
                  const animation=document.createElement('style');
                  animation.textContent='@keyframes osek-spin{to{transform:rotate(360deg)}}#osek-login-status:before{content:"";display:inline-block;width:12px;height:12px;border:2px solid #6669ee;border-top-color:transparent;border-radius:50%;animation:osek-spin .8s linear infinite;margin-right:8px;vertical-align:-2px}';
                  document.head.appendChild(animation);
                  const status=document.createElement('div');status.id='osek-login-status';
                  status.style.cssText='display:none;padding:14px;color:#6669ee;font:600 14px Segoe UI,Arial,sans-serif';
                  status.textContent='Подключаюсь к sdo.i-spo.ru';form.appendChild(status);
                  form.addEventListener('submit',function(event){
                    const u=form.querySelector('input[name="username"]')?.value;
                    const p=form.querySelector('input[name="password"]')?.value;
                    if(!u||!p||!window.pywebview?.api?.capture_credentials)return;
                    event.preventDefault();
                    status.style.display='block';
                    window.pywebview.api.capture_credentials(u,p).finally(()=>form.submit());
                  });
                })()""")
                if self.autologin_attempted:
                    return
                username = keyring.get_password(SERVICE, "__last_user__")
                password = keyring.get_password(SERVICE, username) if username else None
                if username and password:
                    script = """(function(){const u=document.querySelector('input[name="username"]');const p=document.querySelector('input[name="password"]');if(u&&p&&u.form){u.value=%s;p.value=%s;const s=document.getElementById('osek-login-status');if(s)s.style.display='block';u.form.submit()}})()""" % (json.dumps(username), json.dumps(password))
                    self.autologin_attempted = True
                    self.window.evaluate_js(script)
                return
            self.autologin_attempted = False
            if self.pending_credentials and self.window.evaluate_js("!!document.querySelector('.usermenu, a[href*=\"logout.php\"]')"):
                username, password = self.pending_credentials
                keyring.set_password(SERVICE, username, password)
                keyring.set_password(SERVICE, "__last_user__", username)
                self.pending_credentials = None
            menu = [{"icon": icon, "label": label, "url": BASE + path} for icon, label, path in NAV]
            script = r"""(function(menu){
              if(document.getElementById('osek-shell'))return;
              const style=document.createElement('style');
              style.textContent=`#osek-shell{position:fixed;left:0;top:0;bottom:0;width:216px;background:#151a2d;color:#f2f2ff;z-index:2147483647;box-shadow:4px 0 25px #10122344;display:flex;flex-direction:column;font:14px Segoe UI,Arial,sans-serif}#osek-shell *{box-sizing:border-box}#osek-shell .brand{padding:20px 16px 14px;font-size:17px;font-weight:800;border-bottom:1px solid #303754}#osek-shell .identity{padding:12px 16px;border-bottom:1px solid #303754;display:flex;align-items:center;gap:9px;min-height:56px;font-size:12px;line-height:1.25}#osek-shell .identity img{width:32px;height:32px;border-radius:50%;object-fit:cover}#osek-shell .items{overflow:auto;scrollbar-width:thin;flex:1;padding:10px}#osek-shell a{display:block;padding:9px 11px;color:#c6c9e8;text-decoration:none;border-radius:9px;margin:2px 0;font-weight:600}#osek-shell a:hover{background:#363963;color:white}#osek-shell .bottom{padding:12px;color:#9da4c3;border-top:1px solid #303754;font-size:10px}body{margin-left:216px!important;width:calc(100% - 216px)!important}#osek-shell .close-nav{float:right;background:none;color:#ddd;border:0;cursor:pointer}@media(max-width:850px){#osek-shell{width:58px}#osek-shell .text,#osek-shell .identity,#osek-shell .bottom{display:none}#osek-shell .brand{font-size:12px;padding:17px 5px}#osek-shell a{font-size:20px;padding:8px}body{margin-left:58px!important;width:calc(100% - 58px)!important}}`;
              document.head.appendChild(style);
              const box=document.createElement('aside');box.id='osek-shell';
              const brand=document.createElement('div');brand.className='brand';brand.textContent='ОСЭК · Remastered';box.appendChild(brand);
              const identity=document.createElement('div');identity.className='identity';
              const name=document.querySelector('.usermenu .usertext, .logininfo a[href*="profile.php"]');
              const picture=document.querySelector('.usermenu img.userpicture');
              if(picture){const img=document.createElement('img');img.src=picture.src;identity.appendChild(img)}
              const span=document.createElement('span');span.textContent=name?.textContent?.trim()||'Подключаюсь к sdo.i-spo.ru';identity.appendChild(span);box.appendChild(identity);
              const items=document.createElement('div');items.className='items';
              const programLink=[...document.querySelectorAll('a[href]')].find(a=>a.textContent.trim()==='Учебный план'&&/[?&]pid=\d+/.test(a.getAttribute('href')||''));
              const programId=programLink?.getAttribute('href')?.match(/[?&]pid=(\d+)/)?.[1];
              if(programId)localStorage.setItem('osek-program-id',programId);
              const activeProgram=programId||localStorage.getItem('osek-program-id');
              const profileLink=document.querySelector('.usermenu a[href*="/user/profile.php"]');
              for(const entry of menu){
                if(entry.label==='Учебный план'&&activeProgram)entry.url='https://sdo.i-spo.ru/?pid='+activeProgram;
                if(['Документы','Учебный график'].includes(entry.label)&&activeProgram)entry.url+='&pid='+activeProgram;
                if(entry.label==='Уведомления'&&activeProgram)entry.url='https://sdo.i-spo.ru/?action=notice_official&pid='+activeProgram+'&show_notice_tabs=0';
                if(entry.label==='Новости')entry.url='https://sdo.i-spo.ru/?to=news';
                if(entry.label==='Профиль'&&profileLink)entry.url=profileLink.href;
                const native=[...document.querySelectorAll('a[href]')].find(a=>a.textContent.trim()===entry.label&&new URL(a.href).hostname==='sdo.i-spo.ru');
                if(native)entry.url=native.href;
                if(entry.label==='Электронные документы студента'&&!native){
                  const doc=[...document.querySelectorAll('a[href]')].find(a=>a.href.includes('action=ezk'));
                  if(doc)entry.url=doc.href;
                }
              }
              for(const entry of menu){const a=document.createElement('a');a.href=entry.url;a.innerHTML='<span aria-hidden="true">'+entry.icon+'</span> <span class="text"></span>';a.querySelector('.text').textContent=entry.label;items.appendChild(a)}
              document.querySelectorAll('a[target="_blank"][href]').forEach(a=>{if(new URL(a.href).hostname==='sdo.i-spo.ru')a.target='_self'});
              box.appendChild(items);const bottom=document.createElement('div');bottom.className='bottom';bottom.textContent='Неофициальное приложение ОСЭК · пароль хранится локально';box.appendChild(bottom);
              document.body.appendChild(box);
            })(__OSEK_MENU__)""".replace("__OSEK_MENU__", json.dumps(menu, ensure_ascii=False))
            self.window.evaluate_js(script)
        except Exception:
            # A changed page must remain usable even if the added navigation fails.
            pass

    def closing(self):
        if not self.quitting and self.tray:
            self.window.hide()
            return False
        return True

    def quit(self):
        self.quitting = True
        if self.tray:
            self.tray.stop()
        self.window.destroy()

    def startup(self):
        try:
            import pystray
            self.tray = pystray.Icon("ОСЭК-Remastered", icon_image(), "ОСЭК-Remastered", pystray.Menu(
                pystray.MenuItem("Открыть", lambda: self.window.show(), default=True),
                pystray.MenuItem("Выход", self.quit),
            ))
            self.tray.run_detached()
        except Exception:
            self.tray = None


def main():
    shell = Shell()
    data_path = Path(os.environ.get("APPDATA", Path.home())) / "SDO-Reborn"
    webview.start(shell.startup, gui="edgechromium", private_mode=False, storage_path=str(data_path))


if __name__ == "__main__":
    main()
