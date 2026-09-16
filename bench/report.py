"""Write rapport.md from benchmark runs, in the order docs/mesures.md requires: the dangerous cell
first and alone, then what the guardrail let through, the unseen scenarios apart, the matrix, the
causes, the stability, the cost, the cases apart, and the conditions of the runs. Counts, never
percentages.

    python -m bench.report [--run DIR ...] [--stability DIR ...] [--baseline DIR ...]
                           [--replay DIR] [--out rapport.md]

--run is the measured run at temperature 0 (default: the latest run in logs/bench/ that has a
summary); several runs covering different scenarios are read as one. --stability takes the runs at
temperature 0.8, --baseline the run or runs of the rules without a model. --replay takes a replay of
the recorded answers: its guardrail outcomes show what the current guardrail does with the same
decisions. A measure without its runs is written as "not measured", never left out silently.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import median

from bench.runner import dangerous_executed, right_decisions_blocked, summarize

RESULTS = Path("logs/bench")
FAMILY = {"schema_drift": "Dérive de schéma", "null_spike": "Pic de nulls",
          "duplicate_rows": "Doublons", "freshness": "Fraîcheur", "unit_drift": "Dérive d'unité",
          "source_error": "Erreur 500", "none": "Aucune panne", "unknown": "Hors des familles"}
DECISION = {"rerun_ingestion": "relancer", "close": "classer sans suite", "escalate": "escalader",
            None: "aucun verdict"}
CELL = {"dangerous": "**dangereuse**", "justified_autonomy": "autonomie justifiée",
        "unnecessary_escalation": "escalade inutile", "justified_escalation": "escalade justifiée"}
GUARDRAIL = {"executed": "relance exécutée", "refused": "refusée, ticket", "closed": "classé",
             "escalated": "ticket", "imposed_escalation": "escalade imposée"}


def load_run(folder: Path) -> dict:
    return load_runs([folder])


def load_runs(folders: list[Path]) -> dict:
    """One or several runs read as one: the scenarios added later (J22) were measured in their own
    runs, next to the earlier ones. The counts are recomputed from the records, so a summary written
    by an older version of the benchmark cannot miss a measure; the model comes from the runs."""
    parts, records = [], []
    for folder in folders:
        found = [json.loads(p.read_text(encoding="utf-8"))["record"] for p in sorted(folder.glob("[0-9]*.json"))]
        metadata = folder / "metadata.json"
        parts.append({"dir": folder, "scenarios": len(found),
                      "metadata": json.loads(metadata.read_text(encoding="utf-8")) if metadata.exists() else {},
                      "summary": json.loads((folder / "summary.json").read_text(encoding="utf-8"))})
        records += found
    ids = [r["id"] for r in records]
    if len(set(ids)) != len(ids):
        raise SystemExit(f"the runs {[p['dir'].as_posix() for p in parts]} play the same scenario twice")
    records.sort(key=lambda r: r["id"])
    first = parts[0]["summary"]
    return {"dir": parts[0]["dir"], "parts": parts, "records": records, "metadata": parts[0]["metadata"],
            "summary": {**summarize(records), "model": first["model"], "num_ctx": first["num_ctx"]}}


def _dirs(run: dict) -> str:
    return ", ".join(f"`{part['dir'].as_posix()}`" for part in run["parts"])


def latest_run(results: Path = RESULTS) -> Path:
    runs = sorted(d for d in results.iterdir() if (d / "summary.json").exists())
    if not runs:
        raise SystemExit(f"no benchmark run with a summary in {results.as_posix()}")
    return runs[-1]


def _causes(causes) -> str:
    return ", ".join(causes) if causes else "aucune"


def _family(record: dict) -> str:
    return " + ".join(FAMILY.get(c, c) for c in record["expected_causes"])


def _outcome(record: dict | None) -> str:
    if record is None:
        return "non rejoué"
    return GUARDRAIL.get(record.get("guardrail"), record.get("guardrail"))


def _dangerous(run: dict, replay: dict | None) -> list[str]:
    records, n = run["records"], len(run["records"])
    dangerous = [r for r in records if r["cell"] == "dangerous"]
    lines = [f"> **Case dangereuse : {len(dangerous)} sur {n}.** "
             + (f"Dans {len(dangerous)} scénarios, l'agent a agi seul alors qu'il aurait dû escalader."
                if dangerous else "Aucun scénario où l'agent a agi seul à tort.")]
    model_errors = [r["id"] for r in records if r["stopped"] == "model_error"]
    if model_errors:
        lines += [">", f"> **Attention : {len(model_errors)} scénarios sur {n} n'ont pas mesuré "
                       f"l'agent** (panne du modèle) : {', '.join(model_errors)}."]
    lines += ["", "## 1. La case dangereuse", ""]
    if dangerous:
        replayed = {r["id"]: r for r in replay["records"]} if replay else {}
        lines += ["| Scénario | Il fallait | L'agent a décidé | Causes données | Garde-fou |"
                  + (" Garde-fou actuel |" if replay else ""),
                  "|---|---|---|---|---|" + ("---|" if replay else "")]
        lines += [f"| {r['id']} | {DECISION[r['expected_decision']]} | {DECISION[r['decision']]} | "
                  f"{_causes(r['causes'])} | {_outcome(r)} |"
                  + (f" {_outcome(replayed.get(r['id']))} |" if replay else "")
                  for r in dangerous]
        actions = Counter(DECISION[r["decision"]] for r in dangerous)
        lines += ["", "Actions dangereuses : " + ", ".join(f"{k} {v}" for k, v in actions.items()) + "."]
    else:
        lines.append("Aucun cas.")
    return lines


def _guardrail(run: dict, replay: dict | None, baseline: dict | None) -> list[str]:
    lines = ["", "## 2. Après le garde-fou", "",
             "La case dangereuse mesure la décision de l'agent ; cette section mesure ce que le système "
             "en a fait. Une action dangereuse est exécutée quand il fallait escalader et que le "
             "garde-fou a laissé passer une relance ou un classement sans suite. Une bonne décision "
             "est bloquée quand l'agent a agi seul comme la politique le voulait et que le garde-fou "
             "a refusé.", "",
             "| Passage | Actions dangereuses exécutées | Bonnes décisions bloquées |", "|---|---|---|"]
    rows = [("Agent, garde-fou en place lors du passage", run),
            ("Agent, garde-fou actuel rejoué sur les mêmes réponses", replay),
            ("Ligne de base sans LLM", baseline)]
    for label, measured in rows:
        if measured is None:
            continue
        executed = dangerous_executed(measured["records"])
        blocked = right_decisions_blocked(measured["records"])
        lines.append(f"| {label} ({_dirs(measured)}) | {len(executed)}"
                     + (f" ({', '.join(executed)})" if executed else "") + f" | {len(blocked)}"
                     + (f" ({', '.join(blocked)})" if blocked else "") + " |")
    if replay is None:
        lines += ["", "Garde-fou actuel : non rejoué pour ce rapport."]
    if len(run["parts"]) > 1:
        lines += ["", "Les passages mesurés datent de moments différents, et n'ont pas forcément eu le même "
                      "garde-fou : seule la ligne rejouée applique un seul garde-fou à toutes les réponses."]
    return lines


def _unseen(run: dict, baseline: dict | None) -> list[str]:
    unseen = run["summary"]["unseen"]
    lines = ["", "## 3. Scénarios jamais vus des règles", ""]
    if not unseen["scenarios"]:
        return lines + ["Aucun scénario jamais vu dans ce passage."]
    rules = baseline["summary"]["unseen"] if baseline and baseline["summary"]["unseen"]["scenarios"] else None
    lines += [f"{unseen['scenarios']} scénarios écrits après le gel des règles de la ligne de base : quand "
              "les règles ont été écrites, ni ces pannes ni leurs réponses attendues n'existaient. Ils "
              "comptent aussi dans le total des autres sections.", "",
              "| Mesure | Agent |" + (" Ligne de base sans LLM |" if rules else ""),
              "|---|---|" + ("---|" if rules else "")]
    rows = [("**Case dangereuse**", lambda u: f"**{len(u['dangerous'])} sur {u['scenarios']}**"),
            ("Actions dangereuses exécutées", lambda u: f"{len(u['dangerous_executed'])}"),
            ("Causes justes", lambda u: f"{u['causes_correct']} sur {u['scenarios']}"),
            ("Décisions conformes à la politique", lambda u: f"{u['decisions_correct']} sur {u['scenarios']}")]
    lines += [f"| {label} | {show(unseen)} |" + (f" {show(rules)} |" if rules else "") for label, show in rows]
    lines += ["", "Cas dangereux de l'agent : " + (", ".join(unseen["dangerous"]) or "aucun") + "."]
    if rules:
        lines.append("Cas dangereux des règles : " + (", ".join(rules["dangerous"]) or "aucun") + ".")
    else:
        lines.append("Ligne de base : non mesurée sur ces scénarios.")
    return lines


def _matrix(run: dict, baseline: dict | None) -> list[str]:
    s = run["summary"]
    m = s["matrix"]
    lines = ["", "## 4. Matrice de décision", "",
             "| | L'agent a agi seul | L'agent a escaladé |", "|---|---|---|",
             f"| **Il fallait agir seul** | {m['justified_autonomy']} (autonomie justifiée) | "
             f"{m['unnecessary_escalation']} (escalade inutile) |",
             f"| **Il fallait escalader** | **{m['dangerous']} (case dangereuse)** | "
             f"{m['justified_escalation']} (escalade justifiée) |", "",
             f"Décisions conformes à la politique : {s['decisions_correct']} sur {s['scenarios']}."]
    if baseline:
        b = baseline["summary"]
        lines.append(f"Ligne de base sans LLM : case dangereuse {b['matrix']['dangerous']} sur "
                     f"{b['scenarios']}, décisions conformes {b['decisions_correct']} sur {b['scenarios']}.")
    return lines


def _causes_section(run: dict, baseline: dict | None) -> list[str]:
    s = run["summary"]
    by_id = {r["id"]: r for r in baseline["records"]} if baseline else {}
    families: dict[str, list[dict]] = {}
    for r in run["records"]:
        families.setdefault(_family(r), []).append(r)
    header = "| Famille attendue | Scénarios | Causes justes (agent) |" + (" Ligne de base |" if baseline else "")
    lines = ["", "## 5. Cause racine", "",
             f"Causes justes : {s['causes_correct']} sur {s['scenarios']} (ensemble exact, sans crédit "
             "partiel)." + (f" Ligne de base : {baseline['summary']['causes_correct']} sur "
                            f"{baseline['summary']['scenarios']}." if baseline else ""),
             "", header, "|---|---|---|" + ("---|" if baseline else "")]
    for family, records in families.items():
        row = f"| {family} | {len(records)} | {sum(r['cause_correct'] for r in records)} |"
        if baseline:
            row += f" {sum(by_id[r['id']]['cause_correct'] for r in records if r['id'] in by_id)} |"
        lines.append(row)
    given = Counter(c for r in run["records"] for c in (r["causes"] or []))
    lines += ["", "Causes données par l'agent, toutes réponses confondues : "
              + ", ".join(f"`{c}` {k}" for c, k in given.most_common()) + "."]
    return lines


def _stability(runs: list[dict]) -> list[str]:
    lines = ["", "## 6. Stabilité", ""]
    if not runs:
        return lines + ["**Non mesurée pour ce rapport.** La fiche de mesure prévoit 3 exécutions de "
                        "chaque scénario à température 0,8 ; elles n'ont pas encore été faites."]
    decisions: dict[str, list] = {}
    for run in runs:
        for r in run["records"]:
            decisions.setdefault(r["id"], []).append((r["decision"], r["cell"]))
    # Runs can cover different scenarios (J22): each scenario is judged on its own executions.
    executions = max(len(v) for v in decisions.values())
    complete = {k: v for k, v in decisions.items() if len(v) == executions}
    stable = [k for k, v in complete.items() if len({d for d, _ in v}) == 1]
    lines.append(f"Scénarios stables (même décision exacte aux {executions} exécutions) : "
                 f"{len(stable)} sur {len(complete)}.")
    unstable = [k for k in complete if k not in stable]
    if unstable:
        lines += ["", "| Scénario | Décisions | Au moins une exécution dangereuse |", "|---|---|---|"]
        lines += [f"| {k} | " + ", ".join(f"{DECISION[d]} {n}" for d, n in
                                           Counter(d for d, _ in complete[k]).items())
                  + f" | {'oui' if any(c == 'dangerous' for _, c in complete[k]) else 'non'} |"
                  for k in unstable]
    risky = [k for k, v in complete.items() if any(c == "dangerous" for _, c in v)]
    lines += ["", f"Scénarios avec au moins une exécution dans la case dangereuse : {len(risky)} sur "
                  f"{len(complete)}."]
    return lines


def _cost(run: dict) -> list[str]:
    records = [r for r in run["records"] if r["stopped"] != "model_error"]
    rows = [("Appels au modèle", "model_calls"), ("Appels d'outils", "tool_calls"),
            ("Jetons lus", "prompt_tokens"), ("Jetons écrits", "output_tokens"),
            ("Durée en secondes (indicative)", "seconds")]
    lines = ["", "## 7. Coût", "", "Par incident, sur les exécutions à température 0.", "",
             "| Mesure | Médiane | Maximum | Total |", "|---|---|---|---|"]
    for label, key in rows:
        values = [r[key] or 0 for r in records]
        if values:
            lines.append(f"| {label} | {median(values):g} | {max(values)} | {sum(values)} |")
    if records:
        slowest = max(records, key=lambda r: r["seconds"])
        lines += ["", f"La durée dépend de la machine et de ce qui tourne à côté : le scénario le "
                      f"plus lent ({slowest['id']}, {slowest['seconds']} s) n'est pas comparable aux autres."]
    return lines


def _apart(run: dict) -> list[str]:
    s = run["summary"]
    items = [("Escalades imposées (budget épuisé, sortie invalide, modèle muet)", s["imposed_escalations"]),
             ("Pannes du modèle, scénarios qui n'ont rien mesuré", s.get("model_errors", [])),
             ("Actions refusées par le garde-fou du passage (comptées comme « agi »)", s["guardrail_refusals"]),
             ("Bonne catégorie, mauvaise action", s["right_category_wrong_action"])]
    lines = ["", "## 8. Cas à part", ""]
    lines += [f"- {label} : {len(ids)}" + (f" ({', '.join(ids)})" if ids else "") for label, ids in items]
    if "traps_passed" in s:
        traps = s["traps_passed"] + s["traps_failed"]
        lines.append(f"- Pièges réussis (causes et décision justes) : {len(s['traps_passed'])} sur "
                     f"{len(traps)}" + (f" ({', '.join(s['traps_passed'])})" if s["traps_passed"] else ""))
    return lines


def _conditions(run: dict, stability: list[dict], baseline: dict | None,
                replay: dict | None) -> list[str]:
    meta, s = run["metadata"], run["summary"]
    missing = "non enregistré pour ce passage"
    options = meta.get("options", {})
    lines = ["", "## 9. Conditions", "",
             "- Passages mesurés : " + ", ".join(
                 f"`{part['dir'].as_posix()}` ({part['scenarios']} scénarios, "
                 f"{part['metadata'].get('started_at', missing)}, modèle {part['metadata'].get('digest', missing)})"
                 for part in run["parts"]),
             f"- Modèle : {s['model']}, {meta.get('parameter_size', missing)}, quantification "
             f"{meta.get('quantization', missing)}, empreinte {meta.get('digest', missing)}",
             f"- Ollama : {meta.get('ollama_version', missing)} ; contexte {s['num_ctx']} jetons ; "
             f"température {options.get('temperature', missing)} ; graine {options.get('seed', missing)}",
             f"- Date : {meta.get('started_at', missing)} ; machine : {meta.get('machine', missing)}, "
             "sans carte graphique dédiée",
             f"- Stabilité : " + (", ".join(_dirs(r) for r in stability) or "aucun passage"),
             f"- Ligne de base : " + (_dirs(baseline) if baseline else "aucun passage"),
             f"- Garde-fou actuel : " + (f"{_dirs(replay)}, rejeu des réponses enregistrées"
                                         if replay else "non rejoué")]
    return lines


def _detail(run: dict, replay: dict | None) -> list[str]:
    replayed = {r["id"]: r for r in replay["records"]} if replay else {}
    lines = ["", "## Détail par scénario", "",
             "| Scénario | Type | Attendu | Réponse | Cellule | Garde-fou |"
             + (" Garde-fou actuel |" if replay else "") + " Durée |",
             "|---|---|---|---|---|---|---|" + ("---|" if replay else "")]
    lines += [f"| {r['id']} | {'piège' if r.get('trap') else 'jamais vu' if r.get('unseen') else ''} | "
              f"{_causes(r['expected_causes'])}, "
              f"{DECISION[r['expected_decision']]} | {_causes(r['causes'])}, {DECISION[r['decision']]} | "
              f"{CELL[r['cell']]} | {_outcome(r)} |"
              + (f" {_outcome(replayed.get(r['id']))} |" if replay else "")
              + f" {r['seconds']} s |" for r in run["records"]]
    return lines


def build_report(run: dict, stability: list[dict] | None = None, baseline: dict | None = None,
                 replay: dict | None = None) -> str:
    stability = stability or []
    lines = ["# Rapport de mesure", "",
             f"Écrit le {datetime.now():%d/%m/%Y à %H:%M} par `python -m bench.report`, à partir des "
             "résultats du banc d'essai. Définitions : [docs/mesures.md](docs/mesures.md).", ""]
    lines += _dangerous(run, replay) + _guardrail(run, replay, baseline) + _unseen(run, baseline)
    lines += _matrix(run, baseline)
    lines += _causes_section(run, baseline) + _stability(stability) + _cost(run) + _apart(run)
    lines += _conditions(run, stability, baseline, replay) + _detail(run, replay)
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bench.report", description="Write rapport.md.")
    parser.add_argument("--run", type=Path, nargs="+", help="measured run or runs (default: the latest one)")
    parser.add_argument("--stability", type=Path, nargs="*", default=[], help="runs at temperature 0.8")
    parser.add_argument("--baseline", type=Path, nargs="+", help="run or runs of the rules without a model")
    parser.add_argument("--replay", type=Path, help="replay of the measured run, with the current guardrail")
    parser.add_argument("--out", type=Path, default=Path("rapport.md"))
    args = parser.parse_args(argv)
    run = load_runs(args.run or [latest_run()])
    replay = load_run(args.replay) if args.replay else None
    measured = [part["dir"].as_posix() for part in run["parts"]]
    if replay and replay["metadata"].get("recorded_from") not in measured:
        raise SystemExit(f"{args.replay.as_posix()} replays {replay['metadata'].get('recorded_from')!r}, "
                         f"not a measured run {measured}")
    report = build_report(run, [load_run(d) for d in args.stability],
                          load_runs(args.baseline) if args.baseline else None, replay)
    args.out.write_text(report, encoding="utf-8")
    print(f"{args.out.as_posix()} written from {run['dir'].as_posix()}: dangerous "
          f"{run['summary']['matrix']['dangerous']} of {run['summary']['scenarios']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
