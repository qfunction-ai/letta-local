# Workflow history audit (branch-from-old-commit exposure)

**Method:** `git log --all -p -- .github/workflows` — every historical
version of every workflow file, classified by trigger and
permissions/secrets exposure. 123 distinct workflow files have ever been
touched in this tree; **23 carried risky versions** (a `pull_request_target`
trigger, or a write-permission token on push/PR/dispatch triggers).

**The risk being documented:** a branch cut from an OLD commit runs that
commit's workflow versions. History is unchanged; this table records the
exposure for awareness. Most of these files no longer exist on main (upstream
legacy); the ones that do are covered by the permissions lockdown in this PR.

## Commit-range table

| file (newest -> oldest risky range) | triggers |
|---|---|
| alembic-validation.yml | f45ed24fcc73 (newest) - 5a5da527aced (oldest) | pull_request, pull_request_target |
| code_style_checks.yml | f93dc8250b6c - 1a2a7900085f | pull_request, pull_request_target, workflow_dispatch |
| container-publish.yml | 6443b9539d94 - 38eed52c2617 | push (+ workflow_dispatch in older versions) |
| core-integration-tests.yml | 3194ea9ad479 - ffa1d7d60fd0 | pull_request, pull_request_target |
| core-unit-sqlite-test.yaml | 3194ea9ad479 - ffa1d7d60fd0 | pull_request, pull_request_target |
| core-unit-test.yml | 3194ea9ad479 - ffa1d7d60fd0 | pull_request, pull_request_target |
| docker-integration-tests.yaml | 2eb7da9bc397 - ffa1d7d60fd0 | pull_request, pull_request_target |
| fern-docs-preview.yml | f45ed24fcc73 - df4067be7a52 | pull_request, pull_request_target |
| fern-sdk-python-preview.yml | f45ed24fcc73 - db52d765a9ab | pull_request, pull_request_target, push |
| jekyll-gh-pages.yml | 4d4149aed571 - ae4cd97cf645 | push, workflow_dispatch |
| lint-command.yml | bfc57149a992 - bc601ec9b408 | workflow_dispatch |
| semantic-prs.yml | ccc14e2f76f2 - 8b6b67153729 | pull_request_target |
| send-message-integration-tests.yaml | 2eb7da9bc397 - 75efd2068bd5 | pull_request_target |
| send-message-integration-tests.yml | 3194ea9ad479 - ffa1d7d60fd0 | pull_request, pull_request_target |
| send_message_v2_integration_tests.yml | 54bc65a0774e - b8c092dacab5 | pull_request, pull_request_target |
| submodule-auto-update.yaml | 1572633d6ea5 - 90da012eee54 | workflow_dispatch |
| sync.yml | cbba27c04a87 - ccb1a584ef10 | push, workflow_dispatch |
| test-lmstudio.yml | 3194ea9ad479 - ffa1d7d60fd0 | pull_request, pull_request_target, workflow_dispatch |
| test-ollama.yml | 3194ea9ad479 - ffa1d7d60fd0 | pull_request, pull_request_target, workflow_dispatch |
| test-vllm.yml | 3194ea9ad479 - ffa1d7d60fd0 | pull_request, pull_request_target, workflow_dispatch |
| ui-tester.yml | 03e22c49c8a1 - 4e77bd56444c | pull_request, push |
| upstream-sync.yml | 850f73e59b14 | schedule, workflow_dispatch |
| web-ui-tester.yml | b7b9097250b6 - 3323577e7ff4 | pull_request, push |

## Full per-commit table

```
.github/workflows/alembic-validation.yml: 14 risky commits
  f45ed24fcc73  pull_request,pull_request_target   PR_TARGET
  b8e9a80d936d  pull_request,pull_request_target   PR_TARGET
  3f3be5674d2e  pull_request,pull_request_target   PR_TARGET
  cf0de709ed39  pull_request,pull_request_target   PR_TARGET
  2b06b0453d80  pull_request,pull_request_target   PR_TARGET
  49278e583f63  pull_request,pull_request_target   PR_TARGET
  bb8a8f335ccc  pull_request,pull_request_target   PR_TARGET
  d5de00194ac4  pull_request,pull_request_target   PR_TARGET
  e88e04ef97ef  pull_request,pull_request_target   PR_TARGET
  e876dd8b36ab  pull_request,pull_request_target   PR_TARGET
  2eb7da9bc397  pull_request,pull_request_target   PR_TARGET
  6a270ecb8155  pull_request,pull_request_target   PR_TARGET
  e6bbe562484e  pull_request,pull_request_target   PR_TARGET
  5a5da527aced  pull_request,pull_request_target   PR_TARGET
.github/workflows/code_style_checks.yml: 2 risky commits
  f93dc8250b6c  pull_request,pull_request_target,workflow_dispatch   PR_TARGET
  1a2a7900085f  pull_request,pull_request_target,workflow_dispatch   PR_TARGET
.github/workflows/container-publish.yml: 5 risky commits
  6443b9539d94  push  WRITE 
  0219291fd2d7  push,workflow_dispatch  WRITE 
  28a82d0d5f62  push,workflow_dispatch  WRITE 
  865be47921b6  push,workflow_dispatch  WRITE 
  38eed52c2617  push,workflow_dispatch  WRITE 
.github/workflows/core-integration-tests.yml: 55 risky commits
  3194ea9ad479  pull_request,pull_request_target   PR_TARGET
  62673826179e  pull_request,pull_request_target   PR_TARGET
  79b1287c06f0  pull_request,pull_request_target   PR_TARGET
  96deccc45de3  pull_request,pull_request_target   PR_TARGET
  d78e0ccb583d  pull_request,pull_request_target   PR_TARGET
  3e2eba1b0753  pull_request,pull_request_target   PR_TARGET
  693d0bd77469  pull_request,pull_request_target   PR_TARGET
  116461a9d026  pull_request,pull_request_target   PR_TARGET
  8d848c678fe5  pull_request,pull_request_target   PR_TARGET
  dd72c5aad443  pull_request,pull_request_target   PR_TARGET
  db96260bb08f  pull_request,pull_request_target   PR_TARGET
  a9649e64f38e  pull_request,pull_request_target   PR_TARGET
  9f2231f70797  pull_request,pull_request_target   PR_TARGET
  2674df36171a  pull_request,pull_request_target   PR_TARGET
  2b20df17bc22  pull_request,pull_request_target   PR_TARGET
  3d228ccd7994  pull_request,pull_request_target   PR_TARGET
  2b6245ad1218  pull_request,pull_request_target   PR_TARGET
  f317416c93a6  pull_request,pull_request_target   PR_TARGET
  c9940b3149f0  pull_request,pull_request_target   PR_TARGET
  4c3eccd2ff8c  pull_request,pull_request_target   PR_TARGET
  2a29549e9c50  pull_request,pull_request_target   PR_TARGET
  4d44be9370cf  pull_request,pull_request_target   PR_TARGET
  a77c7aac7ab4  pull_request,pull_request_target   PR_TARGET
  b6fda234ba5a  pull_request,pull_request_target   PR_TARGET
  481dc3c193bb  pull_request,pull_request_target   PR_TARGET
  b0cefcbce46c  pull_request,pull_request_target   PR_TARGET
  d0b5d7f47f42  pull_request,pull_request_target   PR_TARGET
  94b0587e8547  pull_request,pull_request_target   PR_TARGET
  40dc70522e00  pull_request,pull_request_target   PR_TARGET
  a4abb2905871  pull_request,pull_request_target   PR_TARGET
  d186afa8a89f  pull_request,pull_request_target   PR_TARGET
  4c927a34a7fd  pull_request,pull_request_target   PR_TARGET
  19d57590e07e  pull_request,pull_request_target   PR_TARGET
  bc36da38cdf0  pull_request,pull_request_target   PR_TARGET
  f6acc52825db  pull_request,pull_request_target   PR_TARGET
  031bb60a965e  pull_request,pull_request_target   PR_TARGET
  32a86277abaa  pull_request,pull_request_target   PR_TARGET
  063c5eac6c0c  pull_request,pull_request_target   PR_TARGET
  e22acfa4ed6e  pull_request,pull_request_target   PR_TARGET
  8c7b83f634d3  pull_request,pull_request_target   PR_TARGET
  5891c7658e85  pull_request,pull_request_target   PR_TARGET
  eb72b3f8e4be  pull_request,pull_request_target   PR_TARGET
  f45ed24fcc73  pull_request,pull_request_target   PR_TARGET
  b8e9a80d936d  pull_request,pull_request_target   PR_TARGET
  3f3be5674d2e  pull_request,pull_request_target   PR_TARGET
  e707eea24028  pull_request,pull_request_target   PR_TARGET
  cf0de709ed39  pull_request,pull_request_target   PR_TARGET
  2b06b0453d80  pull_request,pull_request_target   PR_TARGET
  49278e583f63  pull_request,pull_request_target   PR_TARGET
  bb8a8f335ccc  pull_request,pull_request_target   PR_TARGET
  2eb7da9bc397  pull_request,pull_request_target   PR_TARGET
  6a270ecb8155  pull_request,pull_request_target   PR_TARGET
  e6bbe562484e  pull_request,pull_request_target   PR_TARGET
  5a5da527aced  pull_request,pull_request_target   PR_TARGET
  ffa1d7d60fd0  pull_request,pull_request_target   PR_TARGET
.github/workflows/core-unit-sqlite-test.yaml: 54 risky commits
  3194ea9ad479  pull_request,pull_request_target   PR_TARGET
  62673826179e  pull_request,pull_request_target   PR_TARGET
  9cb5156ac7f0  pull_request,pull_request_target   PR_TARGET
  ed36e055b135  pull_request,pull_request_target   PR_TARGET
  f7755d837a0c  pull_request,pull_request_target   PR_TARGET
  53786ee102b1  pull_request,pull_request_target   PR_TARGET
  ee0170e4466d  pull_request,pull_request_target   PR_TARGET
  462d37813e63  pull_request,pull_request_target   PR_TARGET
  ef9bac78ec1e  pull_request,pull_request_target   PR_TARGET
  89cc53e1c03c  pull_request,pull_request_target   PR_TARGET
  edc968220d4c  pull_request,pull_request_target   PR_TARGET
  f9a3ab988770  pull_request,pull_request_target   PR_TARGET
  71d806f7e29b  pull_request,pull_request_target   PR_TARGET
  35c2045d955f  pull_request,pull_request_target   PR_TARGET
  8ed72d95bd7e  pull_request,pull_request_target   PR_TARGET
  c7645f978060  pull_request,pull_request_target   PR_TARGET
  f496c1e235b4  pull_request,pull_request_target   PR_TARGET
  1267bd96268c  pull_request,pull_request_target   PR_TARGET
  511d6d4cac02  pull_request,pull_request_target   PR_TARGET
  398162719d69  pull_request,pull_request_target   PR_TARGET
  86daf4d044ab  pull_request,pull_request_target   PR_TARGET
  2a79c21467ea  pull_request,pull_request_target   PR_TARGET
  6b50460a2766  pull_request,pull_request_target   PR_TARGET
  2e9807443cf1  pull_request,pull_request_target   PR_TARGET
  54bc65a0774e  pull_request,pull_request_target   PR_TARGET
  f5c9a56079b7  pull_request,pull_request_target   PR_TARGET
  e223728dc280  pull_request,pull_request_target   PR_TARGET
  e36c310b660c  pull_request,pull_request_target   PR_TARGET
  3bf72853ff16  pull_request,pull_request_target   PR_TARGET
  72eb32ff37c6  pull_request,pull_request_target   PR_TARGET
  e96e370c8dfb  pull_request,pull_request_target   PR_TARGET
  18bf3137f20a  pull_request,pull_request_target   PR_TARGET
  ba5fa288e302  pull_request,pull_request_target   PR_TARGET
  13bf2ed1eaca  pull_request,pull_request_target   PR_TARGET
  5270f1895eef  pull_request,pull_request_target   PR_TARGET
  af0636e5eabc  pull_request,pull_request_target   PR_TARGET
  e847bd4ff578  pull_request,pull_request_target   PR_TARGET
  bc9a289bddb9  pull_request,pull_request_target   PR_TARGET
  7d92a29f058a  pull_request,pull_request_target   PR_TARGET
  9c6952c52ee2  pull_request,pull_request_target   PR_TARGET
  bef3877cfb53  pull_request,pull_request_target   PR_TARGET
  b1f5a61bd358  pull_request,pull_request_target   PR_TARGET
  3e561dcb4df4  pull_request,pull_request_target   PR_TARGET
  9cab0096b7fb  pull_request,pull_request_target   PR_TARGET
  f45ed24fcc73  pull_request,pull_request_target   PR_TARGET
  b8e9a80d936d  pull_request,pull_request_target   PR_TARGET
  3f3be5674d2e  pull_request,pull_request_target   PR_TARGET
  e707eea24028  pull_request,pull_request_target   PR_TARGET
  3711b5279c09  pull_request,pull_request_target   PR_TARGET
  2eb7da9bc397  pull_request,pull_request_target   PR_TARGET
  6a270ecb8155  pull_request,pull_request_target   PR_TARGET
  e6bbe562484e  pull_request,pull_request_target   PR_TARGET
  5a5da527aced  pull_request,pull_request_target   PR_TARGET
  ffa1d7d60fd0  pull_request,pull_request_target   PR_TARGET
.github/workflows/core-unit-test.yml: 59 risky commits
  3194ea9ad479  pull_request,pull_request_target   PR_TARGET
  62673826179e  pull_request,pull_request_target   PR_TARGET
  982501f6faf5  pull_request,pull_request_target   PR_TARGET
  4c2749ea42c7  pull_request,pull_request_target   PR_TARGET
  6b3a8d8d2c8c  pull_request,pull_request_target   PR_TARGET
  f7755d837a0c  pull_request,pull_request_target   PR_TARGET
  53786ee102b1  pull_request,pull_request_target   PR_TARGET
  ee0170e4466d  pull_request,pull_request_target   PR_TARGET
  462d37813e63  pull_request,pull_request_target   PR_TARGET
  ef9bac78ec1e  pull_request,pull_request_target   PR_TARGET
  89cc53e1c03c  pull_request,pull_request_target   PR_TARGET
  edc968220d4c  pull_request,pull_request_target   PR_TARGET
  f9a3ab988770  pull_request,pull_request_target   PR_TARGET
  71d806f7e29b  pull_request,pull_request_target   PR_TARGET
  35c2045d955f  pull_request,pull_request_target   PR_TARGET
  8ed72d95bd7e  pull_request,pull_request_target   PR_TARGET
  c7645f978060  pull_request,pull_request_target   PR_TARGET
  f496c1e235b4  pull_request,pull_request_target   PR_TARGET
  1267bd96268c  pull_request,pull_request_target   PR_TARGET
  511d6d4cac02  pull_request,pull_request_target   PR_TARGET
  398162719d69  pull_request,pull_request_target   PR_TARGET
  86daf4d044ab  pull_request,pull_request_target   PR_TARGET
  2a79c21467ea  pull_request,pull_request_target   PR_TARGET
  6b50460a2766  pull_request,pull_request_target   PR_TARGET
  2e9807443cf1  pull_request,pull_request_target   PR_TARGET
  54bc65a0774e  pull_request,pull_request_target   PR_TARGET
  419dc0c8f5d9  pull_request,pull_request_target   PR_TARGET
  6994276abf2a  pull_request,pull_request_target   PR_TARGET
  2f7cf0501f58  pull_request,pull_request_target   PR_TARGET
  3bf72853ff16  pull_request,pull_request_target   PR_TARGET
  72eb32ff37c6  pull_request,pull_request_target   PR_TARGET
  e96e370c8dfb  pull_request,pull_request_target   PR_TARGET
  18bf3137f20a  pull_request,pull_request_target   PR_TARGET
  5270f1895eef  pull_request,pull_request_target   PR_TARGET
  af0636e5eabc  pull_request,pull_request_target   PR_TARGET
  85f474614c02  pull_request,pull_request_target   PR_TARGET
  e847bd4ff578  pull_request,pull_request_target   PR_TARGET
  bc9a289bddb9  pull_request,pull_request_target   PR_TARGET
  0402d5ae1cf5  pull_request,pull_request_target   PR_TARGET
  7d92a29f058a  pull_request,pull_request_target   PR_TARGET
  9c6952c52ee2  pull_request,pull_request_target   PR_TARGET
  6912e46e491c  pull_request,pull_request_target   PR_TARGET
  b1f5a61bd358  pull_request,pull_request_target   PR_TARGET
  3e561dcb4df4  pull_request,pull_request_target   PR_TARGET
  9cab0096b7fb  pull_request,pull_request_target   PR_TARGET
  f45ed24fcc73  pull_request,pull_request_target   PR_TARGET
  b8e9a80d936d  pull_request,pull_request_target   PR_TARGET
  3f3be5674d2e  pull_request,pull_request_target   PR_TARGET
  e707eea24028  pull_request,pull_request_target   PR_TARGET
  cf0de709ed39  pull_request,pull_request_target   PR_TARGET
  3711b5279c09  pull_request,pull_request_target   PR_TARGET
  2b06b0453d80  pull_request,pull_request_target   PR_TARGET
  49278e583f63  pull_request,pull_request_target   PR_TARGET
  bb8a8f335ccc  pull_request,pull_request_target   PR_TARGET
  2eb7da9bc397  pull_request,pull_request_target   PR_TARGET
  6a270ecb8155  pull_request,pull_request_target   PR_TARGET
  e6bbe562484e  pull_request,pull_request_target   PR_TARGET
  5a5da527aced  pull_request,pull_request_target   PR_TARGET
  ffa1d7d60fd0  pull_request,pull_request_target   PR_TARGET
.github/workflows/docker-integration-tests.yaml: 2 risky commits
  2eb7da9bc397  pull_request,pull_request_target   PR_TARGET
  ffa1d7d60fd0  pull_request,pull_request_target   PR_TARGET
.github/workflows/fern-docs-preview.yml: 6 risky commits
  f45ed24fcc73  pull_request,pull_request_target   PR_TARGET
  b8e9a80d936d  pull_request,pull_request_target   PR_TARGET
  3f3be5674d2e  pull_request,pull_request_target   PR_TARGET
  cf0de709ed39  pull_request,pull_request_target   PR_TARGET
  45f8ced218f7  pull_request,pull_request_target   PR_TARGET
  df4067be7a52  pull_request,pull_request_target   PR_TARGET
.github/workflows/fern-sdk-python-preview.yml: 13 risky commits
  f45ed24fcc73  pull_request,pull_request_target,push   PR_TARGET
  b8e9a80d936d  pull_request,pull_request_target,push   PR_TARGET
  3f3be5674d2e  pull_request,pull_request_target,push   PR_TARGET
  cf0de709ed39  pull_request,pull_request_target,push   PR_TARGET
  2355a7cca7c5  pull_request,pull_request_target,push   PR_TARGET
  85332f219bbc  pull_request,pull_request_target,push   PR_TARGET
  8ffeb726e580  pull_request,pull_request_target,push   PR_TARGET
  2b06b0453d80  pull_request,pull_request_target,push   PR_TARGET
  49278e583f63  pull_request,pull_request_target,push   PR_TARGET
  bb8a8f335ccc  pull_request,pull_request_target,push   PR_TARGET
  164b6c640ad2  pull_request,pull_request_target,push   PR_TARGET
  b6dc16e10ea2  pull_request,pull_request_target,push   PR_TARGET
  db52d765a9ab  pull_request,pull_request_target,push   PR_TARGET
.github/workflows/jekyll-gh-pages.yml: 2 risky commits
  4d4149aed571  push,workflow_dispatch  WRITE 
  ae4cd97cf645  push,workflow_dispatch  WRITE 
.github/workflows/lint-command.yml: 23 risky commits
  bfc57149a992  workflow_dispatch  WRITE 
  cb6e1392fafb  workflow_dispatch  WRITE 
  cdc3f3d72eb9  workflow_dispatch  WRITE 
  7c8966df4e33  workflow_dispatch  WRITE 
  1572633d6ea5  workflow_dispatch  WRITE 
  c182adebfbb6  workflow_dispatch  WRITE 
  81f6c7c59ea5  workflow_dispatch  WRITE 
  f6894e89abec  workflow_dispatch  WRITE 
  f45ed24fcc73  workflow_dispatch  WRITE 
  b8e9a80d936d  workflow_dispatch  WRITE 
  3f3be5674d2e  workflow_dispatch  WRITE 
  316b210d270f  workflow_dispatch  WRITE 
  2b06b0453d80  workflow_dispatch  WRITE 
  49278e583f63  workflow_dispatch  WRITE 
  bb8a8f335ccc  workflow_dispatch  WRITE 
  2eb7da9bc397  workflow_dispatch  WRITE 
  6a270ecb8155  workflow_dispatch  WRITE 
  e6bbe562484e  workflow_dispatch  WRITE 
  5a5da527aced  workflow_dispatch  WRITE 
  b41181593dcb  workflow_dispatch  WRITE 
  58c001c26a34  workflow_dispatch  WRITE 
  93c078e21c33  workflow_dispatch  WRITE 
  bc601ec9b408  workflow_dispatch  WRITE 
.github/workflows/semantic-prs.yml: 4 risky commits
  ccc14e2f76f2  pull_request_target   PR_TARGET
  cbfefd44ff2f  pull_request_target   PR_TARGET
  10a59ed4b6fb  pull_request_target   PR_TARGET
  8b6b67153729  pull_request_target   PR_TARGET
.github/workflows/send-message-integration-tests.yaml: 34 risky commits
  2eb7da9bc397  pull_request_target   PR_TARGET
  6a270ecb8155  pull_request_target   PR_TARGET
  e6bbe562484e  pull_request_target   PR_TARGET
  5a5da527aced  pull_request_target   PR_TARGET
  ff718d8c4062  pull_request_target   PR_TARGET
  af7282039d4d  pull_request_target   PR_TARGET
  2a13d78d62b8  pull_request_target   PR_TARGET
  acfdab2ced78  pull_request_target   PR_TARGET
  760522921658  pull_request_target   PR_TARGET
  8c57db782d26  pull_request_target   PR_TARGET
  7a15f6304457  pull_request_target   PR_TARGET
  0fa88028344b  pull_request_target   PR_TARGET
  1ec9ccaf2040  pull_request_target   PR_TARGET
  4d8f7b00e575  pull_request_target   PR_TARGET
  f2a409eeb90b  pull_request_target   PR_TARGET
  17bd5ff2b0bd  pull_request_target   PR_TARGET
  5cf807574f30  pull_request_target   PR_TARGET
  3cce3d761fd5  pull_request_target   PR_TARGET
  202128ec7f55  pull_request_target   PR_TARGET
  639641137dbd  pull_request_target   PR_TARGET
  bde471429414  pull_request_target   PR_TARGET
  b977079d44ed  pull_request_target   PR_TARGET
  1625e3a2f4ee  pull_request_target   PR_TARGET
  e5497b1d0df2  pull_request_target   PR_TARGET
  d7dabd1d5736  pull_request_target   PR_TARGET
  00bc9daa1983  pull_request_target   PR_TARGET
  083f8a5d3e5d  pull_request_target   PR_TARGET
  598a8d05a358  pull_request_target   PR_TARGET
  de4438431f55  pull_request_target   PR_TARGET
  fc72f755f33d  pull_request_target   PR_TARGET
  44a525ecf36e  pull_request_target   PR_TARGET
  8a8b23eaa064  pull_request_target   PR_TARGET
  82af605e742f  pull_request_target   PR_TARGET
  75efd2068bd5  pull_request_target   PR_TARGET
.github/workflows/send-message-integration-tests.yml: 28 risky commits
  3194ea9ad479  pull_request,pull_request_target   PR_TARGET
  62673826179e  pull_request,pull_request_target   PR_TARGET
  d4178705375e  pull_request,pull_request_target   PR_TARGET
  0c7ad204bde3  pull_request,pull_request_target   PR_TARGET
  aa41bd7f6fef  pull_request,pull_request_target   PR_TARGET
  7b30700374d8  pull_request,pull_request_target   PR_TARGET
  0b4e1be1bd8e  pull_request,pull_request_target   PR_TARGET
  f7755d837a0c  pull_request,pull_request_target   PR_TARGET
  89cc53e1c03c  pull_request,pull_request_target   PR_TARGET
  8ed72d95bd7e  pull_request,pull_request_target   PR_TARGET
  398162719d69  pull_request,pull_request_target   PR_TARGET
  54bc65a0774e  pull_request,pull_request_target   PR_TARGET
  72eb32ff37c6  pull_request,pull_request_target   PR_TARGET
  e96e370c8dfb  pull_request,pull_request_target   PR_TARGET
  18bf3137f20a  pull_request,pull_request_target   PR_TARGET
  f45ed24fcc73  pull_request,pull_request_target   PR_TARGET
  b8e9a80d936d  pull_request,pull_request_target   PR_TARGET
  3f3be5674d2e  pull_request,pull_request_target   PR_TARGET
  e707eea24028  pull_request,pull_request_target   PR_TARGET
  cf0de709ed39  pull_request,pull_request_target   PR_TARGET
  2b06b0453d80  pull_request,pull_request_target   PR_TARGET
  49278e583f63  pull_request,pull_request_target   PR_TARGET
  bb8a8f335ccc  pull_request,pull_request_target   PR_TARGET
  2eb7da9bc397  pull_request,pull_request_target   PR_TARGET
  6a270ecb8155  pull_request,pull_request_target   PR_TARGET
  e6bbe562484e  pull_request,pull_request_target   PR_TARGET
  5a5da527aced  pull_request,pull_request_target   PR_TARGET
  ffa1d7d60fd0  pull_request,pull_request_target   PR_TARGET
.github/workflows/send_message_v2_integration_tests.yml: 6 risky commits
  54bc65a0774e  pull_request,pull_request_target   PR_TARGET
  517985ecd707  pull_request,pull_request_target   PR_TARGET
  b5117ff3e716  pull_request,pull_request_target   PR_TARGET
  18bf3137f20a  pull_request,pull_request_target   PR_TARGET
  1f355e6e7ad4  pull_request,pull_request_target   PR_TARGET
  b8c092dacab5  pull_request,pull_request_target   PR_TARGET
.github/workflows/submodule-auto-update.yaml: 29 risky commits
  1572633d6ea5  workflow_dispatch  WRITE 
  f6894e89abec  workflow_dispatch  WRITE 
  73f853b83299  workflow_dispatch  WRITE 
  754477e15fcf  workflow_dispatch  WRITE 
  68949d2c8b51  workflow_dispatch  WRITE 
  77c663de59ab  workflow_dispatch  WRITE 
  c012d241084d  workflow_dispatch  WRITE 
  9c7d6a0d407c  workflow_dispatch  WRITE 
  3077ef8d5afb  workflow_dispatch  WRITE 
  449068c93989  workflow_dispatch  WRITE 
  248028c5884d  workflow_dispatch  WRITE 
  94ae068ab9f0  workflow_dispatch  WRITE 
  1e79aabd5aa0  workflow_dispatch  WRITE 
  4778995009cc  workflow_dispatch  WRITE 
  e3e9f2ffa050  workflow_dispatch  WRITE 
  f35a8e344b7d  workflow_dispatch  WRITE 
  c47d1b1b82df  workflow_dispatch  WRITE 
  c3041633dbcc  workflow_dispatch  WRITE 
  67abf07f8045  workflow_dispatch  WRITE 
  2d68d5f1dea4  workflow_dispatch  WRITE 
  050c6a1dad02  workflow_dispatch  WRITE 
  ce701a67fa22  workflow_dispatch  WRITE 
  1810dd191330  workflow_dispatch  WRITE 
  7a5dd3767c7a  workflow_dispatch  WRITE 
  adf5cceb348f  workflow_dispatch  WRITE 
  2ab6cb7f1afd  workflow_dispatch  WRITE 
  316b210d270f  workflow_dispatch  WRITE 
  162540cb6bec  workflow_dispatch  WRITE 
  90da012eee54  workflow_dispatch  WRITE 
.github/workflows/sync.yml: 7 risky commits
  cbba27c04a87  workflow_dispatch  WRITE 
  ef36fbedb895  workflow_dispatch  WRITE 
  f1757dcc1665  workflow_dispatch  WRITE 
  3da88e179aeb  workflow_dispatch  WRITE 
  0fd92a8f4d79  workflow_dispatch  WRITE 
  d3a2c6aa8fa9  workflow_dispatch  WRITE 
  ccb1a584ef10  push,workflow_dispatch  WRITE 
.github/workflows/test-lmstudio.yml: 25 risky commits
  3194ea9ad479  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  62673826179e  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  f7755d837a0c  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  89cc53e1c03c  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  8ed72d95bd7e  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  398162719d69  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  72eb32ff37c6  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  e96e370c8dfb  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  f45ed24fcc73  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  b8e9a80d936d  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  3f3be5674d2e  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  cf0de709ed39  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  2b06b0453d80  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  49278e583f63  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  bb8a8f335ccc  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  8f3aabd89dc4  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  59baaf90de7b  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  bf84a9905849  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  9c0fa03e8031  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  d6df24505af3  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  2eb7da9bc397  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  6a270ecb8155  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  e6bbe562484e  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  5a5da527aced  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  ffa1d7d60fd0  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
.github/workflows/test-ollama.yml: 20 risky commits
  3194ea9ad479  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  62673826179e  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  f7755d837a0c  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  89cc53e1c03c  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  8ed72d95bd7e  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  398162719d69  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  72eb32ff37c6  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  e96e370c8dfb  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  f45ed24fcc73  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  b8e9a80d936d  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  3f3be5674d2e  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  cf0de709ed39  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  2b06b0453d80  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  49278e583f63  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  bb8a8f335ccc  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  2eb7da9bc397  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  6a270ecb8155  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  e6bbe562484e  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  5a5da527aced  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  ffa1d7d60fd0  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
.github/workflows/test-vllm.yml: 20 risky commits
  3194ea9ad479  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  62673826179e  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  f7755d837a0c  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  89cc53e1c03c  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  8ed72d95bd7e  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  398162719d69  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  72eb32ff37c6  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  e96e370c8dfb  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  f45ed24fcc73  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  b8e9a80d936d  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  3f3be5674d2e  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  cf0de709ed39  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  2b06b0453d80  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  49278e583f63  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  bb8a8f335ccc  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  2eb7da9bc397  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  6a270ecb8155  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  e6bbe562484e  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  5a5da527aced  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
  ffa1d7d60fd0  workflow_dispatch,pull_request,pull_request_target   PR_TARGET
.github/workflows/ui-tester.yml: 4 risky commits
  03e22c49c8a1  push,pull_request  WRITE 
  3cfbd5689990  push,pull_request  WRITE 
  26a64dc99d19  push,pull_request  WRITE 
  4e77bd56444c  push,pull_request  WRITE 
.github/workflows/upstream-sync.yml: 1 risky commits
  850f73e59b14  schedule,workflow_dispatch  WRITE 
.github/workflows/web-ui-tester.yml: 2 risky commits
  b7b9097250b6  push,pull_request  WRITE 
  3323577e7ff4  push,pull_request  WRITE 
```
