import {api} from './client.js';
export const datasets={list:()=>api('/datasets'),get:id=>api('/datasets/'+id),create:data=>api('/datasets',{method:'POST',body:JSON.stringify(data)})};
export const jobs={list:()=>api('/jobs'),get:id=>api('/jobs/'+id),create:data=>api('/jobs',{method:'POST',body:JSON.stringify(data)}),approve:id=>api('/jobs/'+id+'/approve',{method:'POST'})};
export const reports={list:()=>api('/reports'),create:data=>api('/reports',{method:'POST',body:JSON.stringify(data)}),toggle:id=>api('/reports/'+id+'/toggle',{method:'POST'}),deliveries:()=>api('/deliveries')};
export const trials={
 list:()=>api('/trials'),
 get:id=>api('/trials/'+id),
 board:()=>api('/trials/board'),
 create:data=>api('/trials',{method:'POST',body:JSON.stringify(data)}),
 check:(id,data)=>api('/trials/'+id+'/check',{method:'POST',body:JSON.stringify(data)}),
 schedule:(id,data)=>api('/trials/'+id+'/schedule',{method:'PUT',body:JSON.stringify(data)}),
 confirm:id=>api('/trials/'+id+'/confirm',{method:'POST',body:'{}'}),
 start:id=>api('/trials/'+id+'/start',{method:'POST',body:'{}'}),
 cancel:(id,reason)=>api('/trials/'+id+'/cancel',{method:'POST',body:JSON.stringify({reason})}),
 history:id=>api('/trials/'+id+'/history'),
 maintenance:()=>api('/trials/maintenance'),
 createMaintenance:data=>api('/trials/maintenance',{method:'POST',body:JSON.stringify(data)}),
 releaseMaintenance:id=>api('/trials/maintenance/'+id+'/release',{method:'POST',body:'{}'}),
};
