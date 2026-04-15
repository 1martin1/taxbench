import json
import os
import re

from pathlib import Path
from typing import Dict

from openai import OpenAI
from openai.types.chat import ChatCompletion

from prompts import SYS_COMON_PROMPT, build_critic_prompt, SYS_CRITIC_PROMPT, build_fix_prompt, SYS_PLANER_PROMPT, \
    build_executor_prompt, SYS_EXECUTOR_PROMPT, SYS_SEC_PROMPT, generate_tests_prompt, TEST_GENERATE_PROMPT, \
    generate_tests_by_spec_prompt, SYS_PROMPT_VALIDATOR, evaluate_generated_tests_prompt

SPEC_TYPE = 'openapi'
PROMPT_TYPE = 'none'
N_SAMPLES = 1
TEMPERATURE = 0.7
RESULTS_DIR = "../test_validation"
BASE_URL = "***"
API_KEY = "***"
MODEL_NAME = "gpt-5.4"
# ----------------------------------------------------

def print_tokens_statistic(response: ChatCompletion):
    if hasattr(response, 'usage'):
        print(f"Получена статистика от API:")
        print(f"  Prompt tokens: {response.usage.prompt_tokens}")
        print(f"  Completion tokens: {response.usage.completion_tokens}")
        print(f"  Total tokens: {response.usage.total_tokens}")


def call_vllm_api(prompt_text: str, temperature: float, system_prompt: str) -> str:
    client = OpenAI(
        api_key=API_KEY,
        base_url=BASE_URL,
    )

    try:
        print(f"Отправка запроса к модели: {MODEL_NAME} на {BASE_URL}...")

        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt_text}
            ],
            temperature=temperature
        )

        print_tokens_statistic(response)

        generated_text = response.choices[0].message.content
        print("\n--- Ответ LLM (сокращенно) ---")
        print(generated_text[:100] + "...")
        print("-------------------------------\n")

        return generated_text

    except Exception as e:
        error_message = f"# ОШИБКА API: Произошла ошибка при обращении к API: {e}."
        print(error_message)
        return error_message


def extract_code(text):
    pattern = r'<CODE>\s*(.*?)\s*</CODE>'
    matches = re.findall(pattern, text, re.DOTALL)
    if matches:
        return matches[0]
    return text.strip()


def save_single_solution(data_item: Dict, code: str, sample: int = 0):
    scenario_id = data_item['scenario_id']

    output_file_path = Path(
        RESULTS_DIR,
        MODEL_NAME,
        scenario_id,
        f"validation{sample}.py"
    )

    output_file_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file_path, 'w', encoding='utf-8') as f:
        f.write(code.strip())

    print("\n--- РЕЗУЛЬТАТ ---")
    print(f"Код сгенерирован и сохранен в: {output_file_path}")
    print(f"Первые 100 символов кода:\n{code.strip()[:100]}...")
    print("------------------")


if __name__ == "__main__":
    for i in range(0, N_SAMPLES):
        with open("../baxbench_fastapi_only.json", "r") as f:
            data: Dict = json.load(f)
        for d in data:
            with open(f"../prompts/{d['scenario_id']}.txt", "r") as f:
                spec: str = f.read()
            with open(f"../test_results/gpt-5.4_v2/{d['scenario_id']}/tests0.py", "r") as f:
                tests: str = f.read()
            model_output = call_vllm_api(evaluate_generated_tests_prompt(spec, tests), TEMPERATURE, SYS_PROMPT_VALIDATOR)
            save_single_solution(d, model_output, 2)