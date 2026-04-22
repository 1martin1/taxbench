import re
from pathlib import Path
import pandas as pd


def parse_test_results(results_root: str) -> pd.DataFrame:
    results_path = Path.cwd().parent.parent / Path(results_root)

    result_pattern = re.compile(
        r"(Functional|Security)\s+test\s+(\S+)\s+(passed|failed)",
        re.IGNORECASE,
    )

    rows = []

    for log_file in results_path.glob("*/**/sample0/test.log"):
        scenario = log_file.relative_to(results_path).parts[0]
        #print(scenario)
        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        for i, line in enumerate(lines):
            match = result_pattern.search(line)
            if not match:
                continue

            test_type, test_name, status = match.groups()
            status = status.lower()
            success = status == "passed"

            comment = None

            if not success:
                collected_logs = []

                # идём вверх
                j = i - 1
                while j >= 0:
                    if re.search(rf"def\s+{re.escape(test_name)}\b", lines[j]):
                        collected_logs.append(lines[j])
                        break
                    collected_logs.append(lines[j])
                    j -= 1

                collected_logs.reverse()
                comment = "".join(collected_logs).strip()

            rows.append({
                "scenario": scenario,
                "test_type": test_type,
                "test_name": test_name,
                "status": status,
                "success": success,
                "comment": comment,
            })

    df = pd.DataFrame(rows)

    if not df.empty:
        df = df.sort_values(
            by=["scenario", "test_type", "test_name"]
        ).reset_index(drop=True)

    return df
import pandas as pd

pd.set_option("display.max_rows", None)        # показывать все строки
pd.set_option("display.max_columns", None)     # показывать все столбцы
pd.set_option("display.width", None)           # не ограничивать ширину
pd.set_option("display.max_colwidth", 200)    # не обрезать содержимое ячеек
df = parse_test_results("results/gpt-5.4")
print(df)
# validation_df = pd.read_csv("./validation_scores.csv")
# merged_df = df.merge(
#     validation_df[["scenario", "test_name", "score"]],
#     on=["scenario", "test_name"],
#     how="left"   # чтобы не потерять тесты без score
# )
# print(merged_df)
#
# stats = (
#     merged_df
#     .groupby("score")
#     .agg(
#         total_tests=("test_name", "count"),
#         passed_tests=("success", "sum"),      # True считается как 1
#     )
# )
#
# # Добавляем failed
# stats["failed_tests"] = stats["total_tests"] - stats["passed_tests"]
#
# # Если нужно, чтобы были все оценки от 1 до 5 даже если их нет
# stats = stats.reindex(range(1, 6), fill_value=0)
#
# print(stats)