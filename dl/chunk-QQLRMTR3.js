
!function(){try{var e="undefined"!=typeof window?window:"undefined"!=typeof global?global:"undefined"!=typeof globalThis?globalThis:"undefined"!=typeof self?self:{},n=(new e.Error).stack;n&&(e._sentryDebugIds=e._sentryDebugIds||{},e._sentryDebugIds[n]="2da41b16-7e05-5049-8947-e34a088bf1f8")}catch(e){}}();
import{n as a}from"./chunk-S5264IRR.js";import{Da as r,eb as n,ra as e,xa as o}from"./chunk-5NWV34XP.js";var u=(()=>{class i{constructor(){this._http=r(a),this.systemSettings=n(null),this.instantNotifications=n(null)}getNotificationsSettings(){return this._http.get("notifications/config/users").pipe(e(t=>{this.systemSettings.set(t.systemNotifications),this.instantNotifications.set(t.instantNotifications)}))}setNotificationsSettings(t){return this._http.put("notifications/config/users",t)}getTelegramKey(){return this._http.get("notifications/tg_bot/auth_code")}getDeliveryFailures(t){let s={};return t!=null&&(s.days=t),this._http.get("notifications/delivery-failures",{params:s})}initFromUser(t){this.instantNotifications.set(t)}static{this.\u0275fac=function(s){return new(s||i)}}static{this.\u0275prov=o({token:i,factory:i.\u0275fac,providedIn:"root"})}}return i})();export{u as a};
/**i18n:2679c7a8d3bece6d756b211e9e9005dcae23582a5802ad1a8ada3e355c32efef*/
//# 

//# debugId=2da41b16-7e05-5049-8947-e34a088bf1f8
