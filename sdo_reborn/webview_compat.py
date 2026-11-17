"""Keep the WebView helper out of the college site's RequireJS module registry."""

import re

from webview import util

from .diagnostic_log import logger


DOM_JSON_AMD_BRANCH = re.compile(
    r"if \(typeof define === 'function' && define\.amd\) \{ //AMD\s*"
    r"define\(function\(\)\{\s*return factory\(root\);\s*\}\);\s*\} else "
)


def install_requirejs_compatibility():
    load_original = util.load_js_files

    def load_without_amd(window, platform):
        javascript, finish_script = load_original(window, platform)
        javascript, changes = DOM_JSON_AMD_BRANCH.subn("", javascript, count=1)
        if changes:
            logger.debug("webview_dom_json_amd_branch_removed")
        return javascript, finish_script

    util.load_js_files = load_without_amd
