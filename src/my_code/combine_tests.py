import re
from pathlib import Path
from collections import defaultdict

TEST_RESULTS_ROOT = Path("test_results/gpt-5.4_v4")
TEMPLATE_SCENARIOS_ROOT = Path("scenarios")  # папка со вторым видом файлов
OUTPUT_ROOT = Path("tests_combined/combined_scenarios_gpt_v4")  # куда будем сохранять результат


def normalize_name(name: str) -> str:
    return name.replace("_", "").lower()


def extract_blocks(text: str, tag: str) -> list[str]:
    pattern = rf"<{tag}>(.*?)</{tag}>"
    return re.findall(pattern, text, re.DOTALL)


def extract_code_blocks(text: str) -> list[str]:
    return re.findall(r"<CODE>(.*?)</CODE>", text, re.DOTALL)


def clean_list_block(block: str) -> list[str]:
    lines = block.strip().splitlines()
    result = []
    for line in lines:
        line = line.strip()
        if line.startswith("-"):
            result.append(line[1:].strip())
    return result


def process():
    OUTPUT_ROOT.mkdir(exist_ok=True)

    # sample_index -> scenario_name -> aggregated data
    samples_data = defaultdict(lambda: defaultdict(lambda: {
        "codes": [],
        "functional_tests": [],
        "security_tests": []
    }))

    def extract_test_names_from_code(code: str) -> list[str]:
        pattern = r"def\s+([a-zA-Z0-9_]+)\s*\("
        return re.findall(pattern, code)

    # 1️⃣ собираем данные из test_results
    for scenario_dir in TEST_RESULTS_ROOT.iterdir():
        if not scenario_dir.is_dir():
            continue

        scenario_key = normalize_name(scenario_dir.name)

        for test_file in scenario_dir.glob("test*.py"):
            match = re.search(r"tests(\d+)\.py", test_file.name)
            if not match:
                continue

            sample_index = int(match.group(1))

            content = test_file.read_text(encoding="utf-8")

            vulnerabilities_blocks = extract_blocks(content, "VULNERABILITIES")
            code_blocks = extract_code_blocks(content)

            vulnerabilities = (
                clean_list_block(vulnerabilities_blocks[0])
                if vulnerabilities_blocks else []
            )

            for i, code in enumerate(code_blocks):

                extracted_names = extract_test_names_from_code(code)

                for name in extracted_names:
                    if "func" in name.lower():
                        samples_data[sample_index][scenario_key]["functional_tests"].append(name)
                    else:
                        samples_data[sample_index][scenario_key]["security_tests"].append(name)

                comment = ""
                if i < len(vulnerabilities):
                    comment = f"# {vulnerabilities[i]}\n"

                formatted_code = comment + code.strip() + "\n\n"

                samples_data[sample_index][scenario_key]["codes"].append(
                    formatted_code
                )

    # 2️⃣ создаём output структуру
    for sample_index, scenarios in samples_data.items():
        sample_dir = OUTPUT_ROOT / f"sample_{sample_index}"
        sample_dir.mkdir(exist_ok=True)

        for template_file in TEMPLATE_SCENARIOS_ROOT.glob("*.py"):
            template_key = normalize_name(template_file.stem)

            if template_key not in scenarios:
                continue

            template_text = template_file.read_text(encoding="utf-8")

            codes = scenarios[template_key]["codes"]
            functional_tests = scenarios[template_key]["functional_tests"]
            security_tests = scenarios[template_key]["security_tests"]

            # вставляем код
            template_text = template_text.replace(
                "#<PLACE_FOR_TESTS>",
                "\n".join(codes).rstrip()
            )

            # добавляем префикс test_ если его нет
            functional_tests_prefixed = [
                name if name.startswith("test_") else f"test_{name}"
                for name in functional_tests
            ]

            security_tests_prefixed = [
                name if name.startswith("test_") else f"test_{name}"
                for name in security_tests
            ]

            # вставляем functional_tests
            template_text = re.sub(
                r"functional_tests=\[\s*\]",
                "functional_tests=[\n        "
                + ",\n        ".join(functional_tests_prefixed)
                + "\n    ]",
                template_text
            )

            # вставляем security_tests
            template_text = re.sub(
                r"security_tests=\[\s*\]",
                "security_tests=[\n        "
                + ",\n        ".join(security_tests_prefixed)
                + "\n    ]",
                template_text
            )

            output_file = sample_dir / template_file.name
            output_file.write_text(template_text, encoding="utf-8")

        print(f"[OK] Created sample_{sample_index}")


if __name__ == "__main__":
    process()