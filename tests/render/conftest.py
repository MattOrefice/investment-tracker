"""Pre-import modules that touch st.secrets at module level.

AppTest shares sys.modules with the pytest process. If src.config (or any
module that calls st.secrets.get() at import time) is first imported INSIDE
AppTest's ScriptRunContext, the secrets file-watcher registration sends a
delta message that sets _set_page_config_allowed=False — causing
set_page_config() to raise even though it is the first explicit Streamlit
call in the script.

Pre-importing these modules here (in the pytest collection phase, outside any
ScriptRunContext) caches them in sys.modules so the AppTest script's import
statements are no-ops and the file-watcher delta is never sent.
"""
import src.config  # noqa: F401  — triggers st.secrets outside AppTest context
import src.macro   # noqa: F401
import src.shiller  # noqa: F401
