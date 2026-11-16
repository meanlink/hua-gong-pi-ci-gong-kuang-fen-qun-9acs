/** @typedef {{id:number,name:string,source:string,rows:number,checksum:string}} Dataset */
/** @typedef {{id:number,name:string,dataset_id:number,status:string,result:Object,approved:boolean}} AnalysisJob */
/** @typedef {{id:number,name:string,job_id:number,destination:string,next_run:number,enabled:boolean}} ReportSchedule */
/** @typedef {{id:number,code:string,name:string,cleaning_minutes:number,active:boolean,notes:string}} Reactor */
/** @typedef {{reactor_id:number,reactor_code:string,reactor_name:string,start_at:number,end_at:number,cleaning_minutes:number,occupied_until:number,active:boolean,released_at:number|null,position:number}} PilotSlot */
/** @typedef {{conflict_type:'trial'|'maintenance',source_id:number,source_name:string,reactor_code:string,message:string,requested:Object,occupied:Object,overlap:Object}} ScheduleConflict */
/**
 * @typedef {{
 *   id:number,name:string,job_id:number,cluster:string,conditions:Object,context:Object,
 *   status:'draft'|'confirmed'|'started'|'cancelled',occupying:boolean,editable:boolean,
 *   can_confirm:boolean,can_reschedule:boolean,can_start:boolean,can_cancel:boolean,
 *   frozen_conditions:Object,frozen_schedule:Array,warnings:ScheduleConflict[],
 *   slots:PilotSlot[],schedule:PilotSlot[]
 * }} PilotTrial
 */
export {};
