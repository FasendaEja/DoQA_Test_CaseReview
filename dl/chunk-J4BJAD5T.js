
!function(){try{var e="undefined"!=typeof window?window:"undefined"!=typeof global?global:"undefined"!=typeof globalThis?globalThis:"undefined"!=typeof self?self:{},n=(new e.Error).stack;n&&(e._sentryDebugIds=e._sentryDebugIds||{},e._sentryDebugIds[n]="a4f91d9c-9d82-529d-8b86-9d4e753297a5")}catch(e){}}();
import{xa as s}from"./chunk-5NWV34XP.js";var d=(()=>{class r{isErrorState(t,e){return!!(t&&t.invalid&&(t.touched||e&&e.submitted))}static \u0275fac=function(e){return new(e||r)};static \u0275prov=s({token:r,factory:r.\u0275fac,providedIn:"root"})}return r})();var i=class{_defaultMatcher;ngControl;_parentFormGroup;_parentForm;_stateChanges;errorState=!1;matcher;constructor(n,t,e,o,a){this._defaultMatcher=n,this.ngControl=t,this._parentFormGroup=e,this._parentForm=o,this._stateChanges=a}updateErrorState(){let n=this.errorState,t=this._parentFormGroup||this._parentForm,e=this.matcher||this._defaultMatcher,o=this.ngControl?this.ngControl.control:null,a=e?.isErrorState(o,t)??!1;a!==n&&(this.errorState=a,this._stateChanges.next())}};export{d as a,i as b};
/**i18n:2679c7a8d3bece6d756b211e9e9005dcae23582a5802ad1a8ada3e355c32efef*/
//# 

//# debugId=a4f91d9c-9d82-529d-8b86-9d4e753297a5
