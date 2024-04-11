import argparse
import difflib
import json
import os
import re
import subprocess
import tempfile

import requests
import logging

OPENAI_API_KEY = ""

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def check_github_cli():
    try:
        subprocess.run(["gh", "--version"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        print("GitHub CLI not found. Please install it from https://cli.github.com/")
        exit(1)

def check_existing_fork(repo_url):
    try:
        output = subprocess.check_output(["gh", "repo", "list", "--limit", "1000", "--json", "nameWithOwner"], stderr=subprocess.STDOUT)
        repos = json.loads(output.decode())
        for repo in repos:
            if repo["nameWithOwner"].endswith(repo_url.split("/")[-1]):
                return repo["nameWithOwner"]
    except subprocess.CalledProcessError as e:
        if "not logged in to github.com" in e.output.decode():
            print("You are not logged in to GitHub CLI. Please run 'gh auth login' to authenticate.")
        else:
            print("Failed to check existing forks.")
        exit(1)
    return None

def fork_repo(repo_url):
    existing_fork = check_existing_fork(repo_url)
    if existing_fork:
        print(f"Existing fork found: {existing_fork}")
        repo_dir = existing_fork.split("/")[-1]
        subprocess.run(["git", "clone", f"https://github.com/{existing_fork}.git"], check=True)
        print("Existing fork cloned successfully.")
    else:
        try:
            output = subprocess.check_output(["gh", "repo", "fork", repo_url, "--clone"], stderr=subprocess.STDOUT)
            match = re.search(r"Cloning into '(.*)'", output.decode())
            if match:
                repo_dir = match.group(1)
                print("Repository forked and cloned successfully.")
            else:
                print("Failed to fork and clone the repository.")
                exit(1)
        except subprocess.CalledProcessError as e:
            if "not logged in to github.com" in e.output.decode():
                print("You are not logged in to GitHub CLI. Please run 'gh auth login' to authenticate.")
            else:
                print("Failed to fork the repository.")
            exit(1)
    return repo_dir

def get_ignored_files():
    ignored_files = [
        ".DS_Store",
        "node_modules/",
        "__pycache__/",
        "*.pyc",
        "venv/",
        "env/",
        "*.egg-info/",
        "dist/",
        "build/",
        "*.app/",
        "*.ipa/",
        "*.apk/",
        "*.aab/",
        "*.class",
        "*.dex",
        ".git/",
        ".gitignore",
        "LICENSE",
        "README.md",
    ]
    return ignored_files

def read_gitignore(repo_dir):
    gitignore_path = os.path.join(repo_dir, ".gitignore")
    if os.path.exists(gitignore_path):
        with open(gitignore_path, "r") as file:
            return file.read().splitlines()
    return []

def create_codebase_file(repo_dir):
    ignored_files = get_ignored_files() + read_gitignore(repo_dir)
    codebase_file = tempfile.NamedTemporaryFile(delete=False, mode="w", encoding="utf-8")
    
    file_count = 0
    total_size = 0
    
    logger.info(f"Processing repository: {repo_dir}")
    
    for root, dirs, files in os.walk(repo_dir):
        for file in files:
            file_path = os.path.relpath(os.path.join(root, file), repo_dir)
            logger.info(f"Processing file: {file_path}")
            
            if not any(os.path.normpath(os.path.join(repo_dir, ignored_file)) == os.path.normpath(file_path) for ignored_file in ignored_files):
                logger.info(f"File not ignored: {file_path}")
                try:
                    with open(os.path.join(root, file), "r", encoding="utf-8") as f:
                        content = f.read()
                        codebase_file.write(f"// {file_path}\n")
                        codebase_file.write(content)
                        codebase_file.write("\n\n")
                        file_count += 1
                        total_size += len(content)
                except Exception as e:
                    logger.error(f"Error reading file: {file_path}")
                    logger.error(str(e))
            else:
                logger.info(f"File ignored: {file_path}")
                
    codebase_file.close()
    
    if os.path.getsize(codebase_file.name) == 0:
        logger.error("Codebase file is empty.")
        os.remove(codebase_file.name)
        return None
    
    logger.info(f"Combined codebase file created: {codebase_file.name}")
    logger.info(f"Number of files processed: {file_count}")
    logger.info(f"Total size of codebase file: {total_size} bytes")
    
    return codebase_file.name

def improve_code(codebase_file, llm_api):
    with open(codebase_file, "r") as file:
        codebase = file.read()
    
    headers = {
        "Content-Type": "application/json",
        "Authorization":  f"Bearer {OPENAI_API_KEY}"
    }
    
    data = {
        "model": "gpt-4-turbo-preview",
        "messages": [
            {
                "role": "system",
                "content": "You are a helpful assistant that improves code. Your goal is make the code vastly better by making one or two small changes. If all else fails, just make one meaningless change."
            },
            {
                "role": "user",
                "content": f"Please improve the following codebase:\n\n{codebase}"
            }
        ],
        "max_tokens": 4096,
        "temperature": 0.7
    }

    response = requests.post(llm_api, headers=headers, json=data)
    
    if response.status_code == 200:
        improved_code = response.json()["choices"][0]["message"]["content"]
        return improved_code
    else:
        print("Failed to apply code improvements.")
        exit(1)

def apply_improvements(repo_dir, original_codebase, improved_codebase):
    original_files = original_codebase.split("\n\n")
    improved_files = improved_codebase.split("\n\n")
    
    # if len(original_files) != len(improved_files):
    #     logger.error("Mismatch in the number of original and improved files.")
    #     return False

    for original_file, improved_file in zip(original_files, improved_files):
        original_file_path = original_file.split("\n")[0].strip("// ")
        improved_file_content = "\n".join(improved_file.split("\n")[1:])
        
        logger.info(f"Original file path: {original_file_path}")
        logger.info(f"Improved file content: {improved_file_content}")

        file_path = os.path.join(repo_dir, original_file_path)
        
        if os.path.isdir(file_path):
            # Skip directories
            continue
        
        # Create parent directories if they don't exist
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        with open(file_path, "w") as file:
            file.write(improved_file_content)

        logger.info(f"Improvements applied to {file_path}")


def generate_commit_message(diff, llm_api):
    headers = {
        "Content-Type": "application/json",
        "Authorization":  f"Bearer {OPENAI_API_KEY}"
    }
    
    data = {
        "model": "gpt-4-turbo-preview	",
        "messages": [
            {
                "role": "system",
                "content": "You are a helpful assistant that generates commit messages."
            },
            {
                "role": "user",
                "content": f"Please generate a succinct commit message for the following code changes:\n\n{diff}"
            }
        ],
        "max_tokens": 50,
        "temperature": 0.7
    }
    
    response = requests.post(llm_api, headers=headers, json=data)
    
    if response.status_code == 200:
        commit_message = response.json()["choices"][0]["message"]["content"].strip()
        return commit_message + ' -git slut'
    else:
        return "-git slut"

def commit_changes(repo_dir, diff, llm_api):
    if not diff.strip():
      print("No changes to commit.")
      return
    
    commit_message = generate_commit_message(diff, llm_api)
    
    try:
        # Stage changes using git add -p
        git_add_process = subprocess.Popen(["git", "add", "-p"], cwd=repo_dir, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        formatted_diff = diff.replace('\n', '\r\n')
        
        output, error = git_add_process.communicate(input=formatted_diff)

        if git_add_process.returncode != 0:
            logger.error(f"Error occurred during git add -p: {error}")
            return
        
        git_diff_staged_process = subprocess.run(["git", "diff", "--staged", "--quiet"], cwd=repo_dir)
        
        
        if git_diff_staged_process.returncode == 0:
            logger.info("No changes staged for commit.")
            return

        # Commit the staged changes
        subprocess.run(["git", "commit", "-m", commit_message], cwd=repo_dir, check=True)
        # subprocess.run(["git", "push"], cwd=repo_dir, check=True)
        logger.info("Changes committed and pushed.")
    except subprocess.CalledProcessError as e:
        logger.error(f"Error occurred during commit: {str(e)}")

def create_pull_request(repo_dir, base_repo):
    output = subprocess.check_output(["gh", "pr", "create", "--title", "Code Improvements", "--body", "Minor code improvements made using LLM.", "--base", base_repo], cwd=repo_dir)
    print("Pull request created successfully.")

def export_git_sauce_command():
    script_path = os.path.abspath(__file__)
    alias_command = f'alias git-slut="python {script_path}"'
    shell_config_file = os.path.expanduser(f"~/{os.environ['SHELL']}rc")
    
    with open(shell_config_file, "a") as file:
        file.write(f"\n# Git Slut alias\n{alias_command}\n")
    
    print(f"git-slut command exported. Please restart your terminal or run 'source ~/{os.environ['SHELL']}rc' to use it.")

def main():
    parser = argparse.ArgumentParser(description="Git Slut: Become the ultimate github contributions whore")
    parser.add_argument("repo_url", help="URL of the Git repository")
    parser.add_argument("--llm", default="https://api.openai.com/v1/chat/completions", help="LLM API endpoint (default: OpenAI)")
    args = parser.parse_args()
    
    check_github_cli()
    repo_dir = fork_repo(args.repo_url)
    
    codebase_file = create_codebase_file(repo_dir)
    
    if codebase_file is None:
        logger.error("Failed to create codebase file.")
        return
    
    with open(codebase_file, "r") as file:
        original_codebase = file.read()
    
    improved_codebase = improve_code(codebase_file, args.llm)
    os.remove(codebase_file)
    
    logger.info(f"Original codebase length: {len(original_codebase)}")
    logger.info(f"Improved codebase length: {len(improved_codebase)}")

    apply_improvements(repo_dir, original_codebase, improved_codebase)
    
    diff = difflib.unified_diff(original_codebase.splitlines(), improved_codebase.splitlines(), lineterm='')
    diff_output = "\n".join(diff)
    
    if not diff_output.strip():
        logger.error("No improvements made. Aborting commit.")
        return

    commit_changes(repo_dir, diff_output, args.llm)
    create_pull_request(repo_dir, args.repo_url)
    export_git_sauce_command()

if __name__ == "__main__":
    main()