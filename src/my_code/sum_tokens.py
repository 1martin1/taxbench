import re

def count_tokens(file_path: str):
    prompt_total = 0
    completion_total = 0

    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            prompt_match = re.search(r"Prompt tokens:\s*(\d+)", line)
            completion_match = re.search(r"Completion tokens:\s*(\d+)", line)

            if prompt_match:
                prompt_total += int(prompt_match.group(1))

            if completion_match:
                completion_total += int(completion_match.group(1))

    return prompt_total, completion_total


if __name__ == "__main__":
    file_path = "tokens.txt"
    prompt_sum, completion_sum = count_tokens(file_path)

    print(f"Total Prompt tokens: {prompt_sum}")
    print(f"Total Completion tokens: {completion_sum}")