"""mini_marie — TWA query runtime organized by domain (zaha, marie, mop_mof)."""

from __future__ import annotations

import importlib.abc
import importlib.util
import sys

# Old import paths → new domain layout (see README.md).
_PKG_REDIRECTS = {
    "mini_marie.chemistry": "mini_marie.marie.chemistry",
    "mini_marie.twa_city": "mini_marie.zaha.twa_city",
    "mini_marie.sg_old": "mini_marie.zaha.sg_old",
    "mini_marie.mof_case": "mini_marie.mop_mof.mof",
    "mini_marie.twa_mops": "mini_marie.mop_mof.mops",
    "mini_marie.marie_agent": "mini_marie.mop_mof.marie_agent",
    "mini_marie.probe_zaha_stack": "mini_marie.zaha.probe_zaha_stack",
    "mini_marie.zaha.twa_city.probe_endpoint_matrix": "mini_marie.zaha.probe_endpoint_matrix",
    "mini_marie.probe_namespaces": "mini_marie.marie.probe_namespaces",
}


class _LegacyPkgRedirect(importlib.abc.MetaPathFinder):
    """Redirect deprecated mini_marie.* paths to the domain-organized layout."""

    def find_spec(self, fullname, path, target=None):
        for old, new in _PKG_REDIRECTS.items():
            if fullname == old or fullname.startswith(old + "."):
                redirected = new + fullname[len(old) :]
                return importlib.util.find_spec(redirected)
        return None


if not any(isinstance(finder, _LegacyPkgRedirect) for finder in sys.meta_path):
    sys.meta_path.insert(0, _LegacyPkgRedirect())
