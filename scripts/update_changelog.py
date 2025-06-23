import subprocess


def get_commit_details():
    # 获取提交信息，包括哈希、日期和消息
    result = subprocess.run(
        ["git", "log", "--pretty=format:%h %ad %s", "--date=short"],
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()


def get_commit_tags():
    # 获取每个提交的标签信息
    tags = {}
    commits = get_commit_details()
    for commit in commits:
        hash_value = commit.split(" ")[0]
        result = subprocess.run(
            ["git", "tag", "--points-at", hash_value], capture_output=True, text=True
        )
        tag_list = result.stdout.strip().splitlines()
        tags[hash_value] = tag_list
    return tags


def generate_changelog(commit_details, tags):
    changelog_path = "CHANGELOG.md"

    # 生成变更日志内容
    new_changelog_content = []

    # 添加标题
    new_changelog_content.append("# Changelog\n\n## Latest Changes\n\n")

    # 添加每个提交的详细信息
    for detail in commit_details:
        hash_value, date, *message = detail.split(" ")
        message = " ".join(message)  # 重新组合提交信息

        # 检查是否有标签
        if tags.get(hash_value):
            # 添加标签信息
            for tag in tags[hash_value]:
                new_changelog_content.append(f"## {tag}\n\n")

            # 添加提交信息
            new_changelog_content.append(f"### {hash_value} ({date})\n\n")
            new_changelog_content.append(f"{message}\n\n")
        else:
            # 如果没有标签，直接添加提交信息
            new_changelog_content.append(f"### {hash_value} ({date})\n\n")
            new_changelog_content.append(f"{message}\n\n")

    # 写入 CHANGELOG.md
    with open(changelog_path, "w") as file:
        file.writelines(new_changelog_content)


if __name__ == "__main__":
    commit_details = get_commit_details()
    tags = get_commit_tags()  # 获取每个提交的标签信息
    if commit_details:
        generate_changelog(commit_details, tags)
        print("CHANGELOG.md has been updated.")
    else:
        print("No new commits found.")
