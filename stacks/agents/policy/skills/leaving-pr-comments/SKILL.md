---
name: leaving-pr-comments
description: Format and check every GitHub comment, review reply, or dismissal an agent posts on the user's behalf.
---

Start each agent-authored comment with this callout, then the reply outside it:

```md
> [!NOTE]
> 🤖 **<model> responding on behalf of <user>**

Reply starts here.
```

Use the verified public model name, or the harness name if the model is not
exposed. Use the user's GitHub login. Add no second signature, heading or rule.
Apply this to top-level and inline comments, bot replies and written dismissal
reasons; not to PR descriptions, commits, or text the user asked to post verbatim.

Lead with the conclusion, cite the code, check or reproduction, separate verified
facts from judgment, and skip thanks or status narration. Check the full comment
for credentials, customer data and private infrastructure details. Preview the
Markdown, confirm GitHub returned the comment URL, and resolve a thread only
after its reply posted. Do not comment just to say you are still waiting.
