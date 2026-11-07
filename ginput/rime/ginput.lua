-- Bounded whole-input correction. Exact spellings and initials stay ahead of edits.
local M = {}
local index, entries = nil, nil
local function load_lexicon()
  if index then return end
  index, entries = {}, {}
  local path = rime_api.get_user_data_dir() .. '/ginput.lexicon.tsv'
  local file = io.open(path, 'r')
  if not file then return end
  for line in file:lines() do
    local text, pinyin, weight = line:match('^([^\t]+)\t([^\t]+)\t(%d+)$')
    if text then
      local key = pinyin:gsub(' ', '')
      local entry = {text=text, pinyin=pinyin, weight=tonumber(weight)}
      if not index[key] then index[key] = {} end
      table.insert(index[key], entry)
      entries[text .. '\t' .. pinyin] = entry
    end
  end
  file:close()
end
local function aliases(syllable)
  local result = {syllable}
  if syllable:sub(1,2) == 'yu' then table.insert(result, 'v' .. syllable:sub(3)) end
  local alternate = syllable:gsub('^([nl])ue$', '%1ve')
  if alternate ~= syllable then table.insert(result, alternate) end
  return result
end
-- Abbreviation is an intentional spelling, not an edit. Track omitted letters
-- separately so a short initial does not justify arbitrary letter replacement.
local function spelling_cost(raw, pinyin)
  raw = raw:gsub("[ ']+", '')
  local states = {[1]={0,0}}
  for syllable in pinyin:gmatch('%a+') do
    local next_states = {}
    local options = {}
    for _, full in ipairs(aliases(syllable)) do
      options[#options+1] = {full,0,0}
      if #full > 1 then options[#options+1] = {full:sub(1,1),1,#full-1} end
      if full:match('^[zcs]h') and #full > 2 then options[#options+1] = {full:sub(1,2),1,#full-2} end
    end
    for pos, cost in pairs(states) do
      for _, option in ipairs(options) do
        local part, abbr, omitted = option[1], option[2], option[3]
        if raw:sub(pos,pos+#part-1) == part then
          local target = pos + #part
          local value = {cost[1]+abbr,cost[2]+omitted}
          local old = next_states[target]
          if not old or value[1]*100+value[2] < old[1]*100+old[2] then next_states[target]=value end
        end
      end
    end
    states = next_states
  end
  return states[#raw+1]
end
local neighbors = {}
for _, row in ipairs({'qwertyuiop','asdfghjkl','zxcvbnm'}) do
  for i=1,#row do
    neighbors[row:sub(i,i)] = (i>1 and row:sub(i-1,i-1) or '') .. (i<#row and row:sub(i+1,i+1) or '')
  end
end
-- Rime may keep Lua's require cache across schema reloads. Reopen the published
-- lexicon when the translator is rebuilt so automatic dictionary updates apply.
M.translator = {init=function() index=nil;load_lexicon() end}
function M.translator.func(input, seg, env)
  -- No recursive expansion: every lookup is exactly one edit from the raw input.
  if not seg:has_tag('abc') or seg.start~=0 or seg._end~=#env.engine.context.input or #input<4 or #input>32 or not input:match('^[a-z]+$') then return end
  load_lexicon()
  local seen, found = {}, {}
  local function lookup(code, cost)
    if code==input or seen[code] then return end
    seen[code] = true
    for _, entry in ipairs(index[code] or {}) do
      found[#found+1] = {entry=entry,cost=cost}
    end
  end
  for i=1,#input-1 do
    if input:sub(i,i) ~= input:sub(i+1,i+1) then
      lookup(input:sub(1,i-1)..input:sub(i+1,i+1)..input:sub(i,i)..input:sub(i+2),1)
    end
  end
  for i=1,#input do
    local before, after = input:sub(1,i-1), input:sub(i+1)
    for letter in (neighbors[input:sub(i,i)] or ''):gmatch('.') do lookup(before..letter..after,1.15) end
    lookup(before..after,1.25)
  end
  for i=0,#input do
    for byte=97,122 do lookup(input:sub(1,i)..string.char(byte)..input:sub(i+1),i==#input and .75 or 1.25) end
  end
  table.sort(found,function(a,b)
    if a.cost~=b.cost then return a.cost<b.cost end
    if a.entry.weight~=b.entry.weight then return a.entry.weight>b.entry.weight end
    return a.entry.text<b.entry.text
  end)
  local emitted={}
  local count=0
  for _, item in ipairs(found) do
    local entry=item.entry
    if not emitted[entry.text] then
      emitted[entry.text]=true
      local candidate=Candidate('ginput_edit',seg.start,seg._end,entry.text,'纠错 · '..entry.pinyin)
      candidate.preedit=input
      candidate.quality=1-item.cost
      yield(candidate)
      count=count+1
      if count>=12 then break end
    end
  end
end
M.filter = {init=function() load_lexicon() end}
local function context_scores()
  local runtime = os.getenv('XDG_RUNTIME_DIR') or ((os.getenv('HOME') or '') .. '/.cache')
  local active = io.open(runtime .. '/ginput/active-window', 'r')
  if not active then return {} end
  local key = active:read('*l');active:close()
  local file = io.open(runtime .. '/ginput/context.tsv', 'r')
  if not file then return {} end
  local header = file:read('*l') or ''
  local window, timestamp = header:match('^([^\t]+)\t(%d+)$')
  local result = {}
  if window == key and os.time() - tonumber(timestamp) < 1800 then
    for line in file:lines() do
      local text, weight = line:match('^([^\t]+)\t([%d%.%-]+)$')
      if text and tonumber(weight) then result[text] = math.max(-1,math.min(1,tonumber(weight))) end
    end
  end
  file:close()
  return result
end
function M.filter.func(translation, env)
  local context=env.engine.context
  local raw=context.input
  local scores=context_scores()
  local batch, ordinal={},0
  local function classify(candidate)
    local full=candidate._end==#raw
    local code=candidate.comment or ''
    local item={candidate=candidate,category=4,score=0,order=ordinal}
    if candidate.type=='ginput_edit' then
      item.category=2;item.score=candidate.quality
    elseif full and candidate.type~='sentence' and code:match('^[a-z ]+$') then
      local typed=raw:sub(candidate.start+1,candidate._end)
      local cost=spelling_cost(typed,code)
      if cost then
        if cost[1]==0 then item.category=0
        else
          item.category=1
          local entry=entries[candidate.text..'\t'..code]
          item.score=-cost[1]-.04*cost[2]+.1*math.log(1+(entry and entry.weight or 0))
        end
      else item.category=3 end
      candidate.comment=''
    elseif full then item.category=3 end
    -- Context may break spelling ties, but can never pay for a spelling edit.
    item.context=scores[candidate.text] or 0
    return item
  end
  for candidate in translation:iter() do
    ordinal=ordinal+1
    if ordinal<=96 then batch[#batch+1]=classify(candidate)
    else
      if batch then
        table.sort(batch,function(a,b)
          if a.category~=b.category then return a.category<b.category end
          if a.score~=b.score then return a.score>b.score end
          if a.context~=b.context then return a.context>b.context end
          return a.order<b.order
        end)
        for _, item in ipairs(batch) do yield(item.candidate) end
        batch=nil
      end
      if candidate.type~='ginput_edit' then candidate.comment='' end
      yield(candidate)
    end
  end
  if batch then
    table.sort(batch,function(a,b)
      if a.category~=b.category then return a.category<b.category end
      if a.score~=b.score then return a.score>b.score end
      if a.context~=b.context then return a.context>b.context end
      return a.order<b.order
    end)
    for _, item in ipairs(batch) do yield(item.candidate) end
  end
end
return M
