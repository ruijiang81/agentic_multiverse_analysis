"""Report generator of the synthetic fixture run (stdlib only; every number is computed)."""
import csv
import json
from pathlib import Path

RUN = Path(__file__).resolve().parent


def rates(path):
    rows = list(csv.DictReader((RUN / path).open()))
    ok = [r for r in rows if not r["err"].strip()]
    pos = [r for r in ok if float(r["estimate"]) > 0]
    surv = [r for r in pos if float(r["p_value"]) < 0.05]
    return len(rows), len(rows) - len(ok), len(pos) / len(ok), len(surv) / len(ok)


audit = json.loads((RUN / "audit.json").read_text())
n, n_err, sign, surv = rates("grid_results.csv")
cn, cn_err, csign, csurv = rates("companion_results.csv")
lines = [
    "# Report (synthetic fixture)",
    "",
    f"Grid: {n} cells, {n_err} error cell(s); inference: {audit['inference_designation']['method']}.",
    f"Sign-consistency rate: {sign:.3f}",
    f"Survival rate: {surv:.3f}",
    f"Companion survival rate: {csurv:.3f} ({cn} cells, sign {csign:.3f})",
    "",
]
(RUN / "report.md").write_text("\n".join(lines), encoding="utf-8")
print("wrote report.md")
