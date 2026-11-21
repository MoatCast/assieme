# Who ports each module

One Rust module per Python module in `assieme/`. The foundation wrote every file, declared every name another session
calls, and ported the shared helpers. Each session fills in the bodies of the modules it owns, and only those
(see the spec's "Rules for working in parallel"). `record/cross_names.py` reads this table.

| Module | Owner |
|---|---|
| status | A |
| adapters | A |
| detect | A |
| limits | A |
| report | A |
| reports | A |
| live | A |
| teammap | B |
| panes | B |
| answers | B |
| activity | B |
| menubar | B |
| prompts | B |
| fixes | C |
| quiet | C |
| budget | C |
| tasks | C |
| actions | C |
| preset | D |
| team | D |
| launch | D |
| newteam | D |
| worktree | D |
| permissions | D |
| brief | D |
| tools | D |
| agenttools | D |
| plugin | D |
| container | D |
| scripts | D |
| musicians | D |
| review | E |
| preview | E |
| remote | E |
| connect | E |
| machine | E |
| bridge | E |
| herdr | F |
| bringover | F |
| setup | F |
| antigravity | F |
| improve | F |
| schedule | F |
| mcpserver | G |
| mcptools | G |
| hook | G |
| askhook | G |
| ask | G |
| cli | G |
| serve | G |
| model | foundation |
| settings | foundation |
| runtime | foundation |
| rules | foundation |

## Names the foundation ported inside other sessions' modules

| Name | Owner |
|---|---|
| quiet.state_dir | foundation |
| quiet._read_json | foundation |
| quiet._write_json | foundation |
| quiet._locked | foundation |
| setup.agent_path | foundation |

Session H (the replay harness) and session I (the cutover machinery) own no Python module.
