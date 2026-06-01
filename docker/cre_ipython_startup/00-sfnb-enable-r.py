# Auto-register %%R when the notebook kernel starts on a sfnb CRE image.
# Installed to ~/.ipython/profile_default/startup/ at image build time.

import os

if os.environ.get("SFNB_CUSTOM_RUNTIME", "").strip().lower() not in (
    "1",
    "true",
    "yes",
):
    pass
else:
    try:
        try:
            from sfnb_multilang.helpers.r_helpers import setup_r_environment
        except ImportError:
            import sys

            sys.path.insert(0, os.environ.get("SFNB_HELPERS_PATH", "/opt/sfnb/helpers"))
            from r_helpers import setup_r_environment

        _res = setup_r_environment()
        if _res.get("success"):
            print(
                f"[sfnb CRE] %%R ready — {_res.get('r_version', 'R')} "
                f"(magic registered={_res.get('magic_registered')})"
            )
        else:
            print(f"[sfnb CRE] %%R setup failed: {_res.get('errors')}")
    except Exception as exc:
        print(f"[sfnb CRE] %%R startup error: {exc}")
