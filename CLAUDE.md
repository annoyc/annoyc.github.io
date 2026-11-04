# Git 提交规范

- 所有提交的作者和提交者都必须是 `annoyc <yc2hangycz@gmail.com>`，禁止使用 Claude 或任何其他身份。提交前先执行：
  `git config --global user.name annoyc && git config --global user.email yc2hangycz@gmail.com`
- 提交信息、PR 描述中不得出现 `Co-Authored-By`、`Claude-Session`、"Generated with Claude Code" 或任何其他署名/会话链接。
- 提交后用 `git log -1 --format='%an <%ae> | %cn <%ce>%n%B'` 确认作者和提交说明符合以上要求。
