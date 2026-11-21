import sys
from pathlib import Path


def main():
    resources = Path(sys.executable).parent
    sys.path.insert(0, str(resources / "packages"))
    import keyring
    from keyring.backends.Windows import WinVaultKeyring

    keyring.set_keyring(WinVaultKeyring())
    from sdo_reborn.app import main as start, resource_path

    if sys.argv[1:] == ["--packaging-check"]:
        import clr
        import pypdfium2
        import requests
        from sdo_reborn.summary_pdf import summary_pdf
        from webview.platforms import winforms, edgechromium
        from webview.util import interop_dll_path

        assert resource_path("web/index.html").is_file()
        assert resource_path("assets/icon.png").is_file()
        assert resource_path("sdo_reborn/install_update.ps1").is_file()
        assert Path(interop_dll_path("Microsoft.Web.WebView2.Core.dll")).is_file()
        assert WinVaultKeyring.priority > 0
        assert Path(requests.certs.where()).is_file()
        with pypdfium2.PdfDocument(summary_pdf("Проверка", "Кириллица")) as document:
            assert len(document) == 1
        return
    start()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        if sys.argv[1:] != ["--packaging-check"]:
            raise
        import traceback
        Path(sys.executable).with_name("packaging-check.log").write_text(traceback.format_exc(), encoding="utf-8")
        sys.exit(1)
