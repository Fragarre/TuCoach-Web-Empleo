from __future__ import annotations

import json

from app.gva_adc import inventariar_adc_gva


def main() -> None:
    resultado = inventariar_adc_gva()
    print(json.dumps(resultado, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
