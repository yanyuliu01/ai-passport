import test from 'node:test';
import assert from 'node:assert/strict';
import { PairingPrompts, type PairingRequest } from '../core/pairing-prompts.js';
const turn = () => new Promise(resolve => setTimeout(resolve, 10));
test('pairing prompts are serialized once, and cancellation is an explicit denial', async () => {
  let pending: PairingRequest[] = [{ id:'account:phone',name:'Phone',fingerprint:'a',expires:Date.now()+60000 }];
  let answer!: (allowed: boolean) => void;const prompts: string[]=[], decisions: boolean[]=[];
  const controller=new PairingPrompts(()=>pending,request=>{prompts.push(request.id);return new Promise(resolve=>{answer=resolve;});},async(_,allowed)=>{decisions.push(allowed);pending=[];});
  controller.update();controller.update();assert.deepEqual(prompts,['account:phone']);assert.deepEqual(decisions,[]);
  answer(false);await turn();assert.deepEqual(decisions,[false]);controller.update();await turn();assert.equal(prompts.length,1);
});
test('a stale dialog cannot approve an expired, removed or replaced invitation',async()=>{
  let pending:PairingRequest[]=[{id:'legacy:phone',name:'Phone',fingerprint:'a',expires:Date.now()+60000}];
  const answers:((value:boolean)=>void)[]=[],decisions:string[]=[];
  const controller=new PairingPrompts(()=>pending,()=>new Promise(resolve=>answers.push(resolve)),async request=>{decisions.push(request.fingerprint);pending=[];});
  controller.update();pending=[{...pending[0],fingerprint:'b'}];answers[0](true);await turn();assert.deepEqual(decisions,[]);assert.equal(answers.length,2);
  pending=[];answers[1](true);await turn();assert.deepEqual(decisions,[]);
  pending=[{id:'legacy:expired',name:'Phone',fingerprint:'c',expires:Date.now()-1}];controller.update();await turn();assert.equal(answers.length,2);
});
