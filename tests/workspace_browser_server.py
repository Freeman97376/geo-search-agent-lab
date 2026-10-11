"""Test-only local server. Both paid search and model dispatch are fixtures."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from test_workspace import fake_planner, response
from geo_search_lab.workspace.service import Workspace
from geo_search_lab.workbench.api import create_app

root = Path(sys.argv[1])
workspace = Workspace(root / "workspace", planner=fake_planner, fetcher=response)
workspace.session_key = ""  # Never inherit a real key in this test server.
uvicorn.run(create_app(root, workspace_mode=True, workspace=workspace), host="127.0.0.1", port=int(sys.argv[2]))
