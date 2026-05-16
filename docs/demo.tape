# vhs tape for the README hero demo.
# Render with:  vhs docs/demo.tape
# Output:       docs/demo.gif

Output docs/demo.gif

Set Shell "zsh"
Set FontSize 16
Set Width 1100
Set Height 620
Set TypingSpeed 50ms
Set PlaybackSpeed 1.0
Set Theme "Dracula"
Set Padding 20

Hide
Type "export PS1='$ '" Enter
Type "setopt interactive_comments" Enter
Type "clear" Enter
Sleep 400ms
Show

Type "# archy: one number for your Python codebase architectural health"
Sleep 800ms
Enter
Type "uv run archy score ."
Sleep 400ms
Enter
Sleep 4500ms

Type "# refactor priority = cyclomatic complexity x git churn"
Sleep 800ms
Enter
Type "uv run archy hotspots . | head -12"
Sleep 400ms
Enter
Sleep 4500ms

Type "# expose 14 tools to Claude Code, Cursor, any MCP client"
Sleep 800ms
Enter
Type "uv run archy mcp --help | head -10"
Sleep 400ms
Enter
Sleep 3500ms
