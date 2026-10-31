void (async () => {
  try {
    for(let i=0;i<20&&!studioState.snapshot;i++)await new Promise(r=>setTimeout(r,100));
    const expected=window.__visibilityProject;
    for(let i=0;i<3;i++)await refresh();
    if(studioState.project!==expected)throw Error("Finished project was replaced by another selection");
    if(![...$("projects").options].some(option=>Number(option.value)===expected))throw Error("Finished project disappeared from selector");
    if(studioState.snapshot.counts.translated!==1)throw Error("Saved translation is unavailable");
    window.__featureProbe={passed:true,project:studioState.project,checks:{completed_project_visible:true,selection_survives_refresh:true,translation_available:true}};
  }catch(error){window.__featureProbe={passed:false,error:error.message,stack:error.stack};}
})();
