# External hourly trigger (primary scheduler)

GitHub's native `schedule` cron is best-effort and can delay runs by hours. Use an
external cron service to call `workflow_dispatch` every hour. The workflow still
keeps `cron: "31 * * * *"` as a backup.

## 1. Create a fine-grained PAT

GitHub → **Settings → Developer settings → Fine-grained tokens → Generate**

| Field | Value |
|-------|-------|
| Repository access | Only `gliu-nova/twitter-bot` |
| Permissions | **Actions: Read and write** |

Copy the token (shown once).

## 2. Test dispatch locally

```bash
export GH_DISPATCH_TOKEN="github_pat_..."
./scripts/trigger-workflow.sh
```

You should see `Dispatched gliu-nova/twitter-bot workflow 299012131`. Check the
**Actions** tab — a new run with `workflow_dispatch` should start within seconds.

## 3. Configure cron-job.org (free)

1. Sign up at [cron-job.org](https://cron-job.org)
2. **Create cronjob**
3. Settings:

| Field | Value |
|-------|-------|
| Title | Twitter Bot hourly |
| URL | `https://api.github.com/repos/gliu-nova/twitter-bot/actions/workflows/299012131/dispatches` |
| Schedule | Every hour at **:31** (matches backup cron; 9:31 AM ET ≈ market open) |
| Request method | **POST** |
| Request body | `{"ref":"main","inputs":{"source":"external-cron"}}` |
| Content-Type | `application/json` |

4. **Headers** (add two):

| Header | Value |
|--------|-------|
| `Accept` | `application/vnd.github+json` |
| `Authorization` | `Bearer YOUR_PAT_HERE` |

5. Save and run once manually from cron-job.org.

## 4. Verify

- Actions tab shows hourly `workflow_dispatch` runs with source `external-cron`
- Log step **Log trigger** prints `source=external-cron`
- Native `schedule` runs may still appear sporadically as backup (OK if both run — concurrency queues them)

## Notes

- `source=external-cron` skips Twitter `get_me` on validate (saves X API credits). Use **Run workflow → manual** in GitHub UI for a full auth check.
- Rotate the PAT if exposed. Never commit tokens to the repo.