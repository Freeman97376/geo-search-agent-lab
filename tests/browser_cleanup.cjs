const {execFileSync}=require('node:child_process');

// On Windows the venv launcher can own a second Python process. Terminate the
// exact child tree created by the test, before removing its private temp store.
async function stopTestServer(child){
  if(child.exitCode!==null)return;
  const exited=new Promise(resolve=>child.once('exit',resolve));
  if(process.platform==='win32')execFileSync('taskkill',['/PID',String(child.pid),'/T','/F'],{windowsHide:true,stdio:'pipe'});
  else child.kill();
  await exited;
}
module.exports={stopTestServer};
