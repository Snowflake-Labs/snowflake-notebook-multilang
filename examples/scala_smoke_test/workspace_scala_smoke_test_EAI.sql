-- =========================================================================
-- EAI Setup: workspace_scala_smoke_test
-- =========================================================================
-- Network Rule and External Access Integration for this notebook.
-- Run as a role with CREATE INTEGRATION privileges (e.g. ACCOUNTADMIN).
--
-- After running, attach the EAI to your notebook in Snowsight:
--   Notebook settings > External access > WORKSPACE_SCALA_SMOKE_TEST_EAI
-- =========================================================================

CREATE OR REPLACE NETWORK RULE WORKSPACE_SCALA_SMOKE_TEST_NR
  MODE = EGRESS
  TYPE = HOST_PORT
  VALUE_LIST = (
    'api.anaconda.org',
    'api.github.com',
    'binstar-cio-packages-prod.s3.amazonaws.com',
    'codeload.github.com',
    'conda.anaconda.org',
    'files.pythonhosted.org',
    'github.com',
    'micro.mamba.pm',
    'objects.githubusercontent.com',
    'pypi.org',
    'release-assets.githubusercontent.com',
    'repo.anaconda.com',
    'repo1.maven.org'
  );

CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION WORKSPACE_SCALA_SMOKE_TEST_EAI
  ALLOWED_NETWORK_RULES = (WORKSPACE_SCALA_SMOKE_TEST_NR)
  ENABLED = TRUE;

-- Grant usage to your notebook role (uncomment and adjust):
-- GRANT USAGE ON INTEGRATION WORKSPACE_SCALA_SMOKE_TEST_EAI TO ROLE <YOUR_ROLE>;
