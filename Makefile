.PHONY: setup-pre-commit setup-skills

PRE_COMMIT_VERSION := 4.6.0
SKILLS_REF ?= 0fe6545b950671641b2b14b44eb56678fea6c6b4
THIRD_PARTY_SKILLS := github-actions-efficiency github-actions-hardening \
	github-actions-runtime-upgrade-conventions create-github-action-workflow-specification

setup-pre-commit:
	@command -v uv >/dev/null 2>&1 || { echo "uv is required for pre-commit and the Python script hook"; exit 1; }
	@command -v uvx >/dev/null 2>&1 || { echo "uvx is required for the Python script pre-commit hook"; exit 1; }
	@if ! command -v pre-commit >/dev/null 2>&1; then \
		uv tool install --force pre-commit==$(PRE_COMMIT_VERSION) || exit 1; \
	fi
	@command -v go >/dev/null 2>&1 || { echo "Go is required for the actionlint pre-commit hook"; exit 1; }
	@if [ -n "$$GITHUB_PATH" ]; then printf '%s\n' "$$HOME/.local/bin" >> "$$GITHUB_PATH"; fi
	PATH="$$HOME/.local/bin:$$PATH" pre-commit install

setup-skills:
	@command -v gh >/dev/null 2>&1 || { echo "gh is required for setup-skills"; exit 1; }
	@mkdir -p .agents/skills
	@for skill in $(THIRD_PARTY_SKILLS); do \
		gh skills install github/awesome-copilot "$$skill" \
			--pin "$(SKILLS_REF)" --dir "$(CURDIR)/.agents/skills" --force || exit 1; \
	done
	@for dir in skills/*/; do \
		[ -d "$$dir" ] || continue; \
		name=$${dir%/}; name=$${name##*/}; \
		target=".agents/skills/$$name"; \
		if [ -e "$$target" ] && [ ! -L "$$target" ]; then \
			echo "refusing: $$target exists and is not a symlink"; exit 1; \
		fi; \
		ln -sfn "../../skills/$$name" "$$target" || exit 1; \
	done
	@mkdir -p .claude/skills
	@for skill in $(THIRD_PARTY_SKILLS); do \
		if [ -e ".claude/skills/$$skill" ] && [ ! -L ".claude/skills/$$skill" ]; then \
			echo "refusing: .claude/skills/$$skill exists and is not a symlink"; exit 1; \
		fi; \
		ln -sfn "../../.agents/skills/$$skill" ".claude/skills/$$skill" || exit 1; \
	done
