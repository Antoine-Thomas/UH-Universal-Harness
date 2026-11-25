"""Un fournisseur personnalisé doit porter son budget de nouvelle tentative.

`e0f86fb` a posé `self.retry_attempts` dans `Provider.__init__`. Or
`OpenAICompatProvider` — le seul chemin des fournisseurs créés par
`add_provider` — redéfinit `__init__` sans appeler le parent, et omettait donc ce
champ. Conséquence : dès qu'un tel fournisseur passait par le chemin de nouvelle
tentative (`attempts=self.retry_attempts`), Python levait `AttributeError`.

Deux tests, et le premier est celui qui échouait : le second garde le parent.
"""

from __future__ import annotations

from uh.providers import RETRY_ATTEMPTS
from uh.providers.omnirouter import OmniRouterProvider
from uh.providers.openai_compat import OpenAICompatProvider


def test_a_configured_provider_carries_its_retry_budget() -> None:
    provider = OpenAICompatProvider(model="m", base_url="http://127.0.0.1:9/v1")

    assert provider.retry_attempts == RETRY_ATTEMPTS


def test_the_parent_still_sets_it() -> None:
    provider = OmniRouterProvider(
        model="m", base_url="http://127.0.0.1:9/v1", api_key="k"
    )

    assert provider.retry_attempts == RETRY_ATTEMPTS
