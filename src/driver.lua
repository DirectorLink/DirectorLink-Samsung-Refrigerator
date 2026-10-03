--[[
  Samsung Refrigerator (DirectorLink) - Control4 DriverWorks driver
  DirectorLink - https://directorlink.io - Apache License 2.0

  Controls Samsung Wi-Fi refrigerators through the SmartThings cloud REST API,
  using the same capabilities and commands as Home Assistant's smartthings
  integration:

    Power Cool     samsungce.powerCool   activate / deactivate   (fallback: refrigeration.setRapidCooling)
    Power Freeze   samsungce.powerFreeze activate / deactivate   (fallback: refrigeration.setRapidFreezing)
    Sabbath Mode   samsungce.sabbathMode on / off
    Ice Maker      switch on component "icemaker"
    Setpoints      thermostatCoolingSetpoint.setCoolingSetpoint on "cooler" / "freezer" / "onedoor"
    Water filter   custom.waterFilter.resetWaterFilter
    Status         temperatures, doors, FlexZone mode, water filter, power/energy, health

  Every feature is detected per refrigerator: capabilities that are missing,
  listed in custom.disabledCapabilities, or on a component listed in
  custom.disabledComponents are shown as "Not available".

  Auth: SmartThings OAuth2 (API_ONLY app created with a Personal Access Token).
  Access tokens last 24h; refresh tokens rotate on every use, so every refresh
  result is persisted immediately and refreshes are serialized.

  Lua 5.1 / LuaJIT compatible.
]]

------------------------------------------------------------------------------
-- Constants
------------------------------------------------------------------------------
local API_BASE        = "https://api.smartthings.com/v1"
local AUTHORIZE_URL   = "https://api.smartthings.com/oauth/authorize"
local TOKEN_URL       = "https://auth-global.api.smartthings.com/oauth/token"
local OAUTH_SCOPES    = { "r:devices:*", "x:devices:*" }
DRIVER_VERSION        = "dev"   -- scripts/build.py stamps the VERSION file here
local USER_AGENT      = "DirectorLink-SamsungRefrigerator/" .. DRIVER_VERSION
local DISCOVER_PROMPT = "(run Discover Refrigerators)"

local RENEW_MARGIN_S    = 4 * 3600          -- renew when less than this is left
local HOUSEKEEPING_MS   = 30 * 60 * 1000    -- token check interval
local RENEW_RETRY_MS    = 5 * 60 * 1000     -- retry after a transient renew failure
local CONFIRM_DELAYS_S  = { 3, 7, 15, 30 }  -- status re-reads after a command
local ERROR_FLASH_MS    = 15000

local STATUS_BINDING = 5001                 -- "Samsung Refrigerator" status tile (primary proxy)

local PERSIST_AUTH  = "ST_AUTH"
local PERSIST_LAST  = "ST_LAST"

-- On/off features. Variants are tried in order; the first one available on
-- the refrigerator is used.
FEATURES = {
  {
    key = "powerCool", name = "Power Cool", binding = 5002, button = 300,
    var = "POWER_COOL", cond = "POWER_COOL",
    variants = {
      { comp = "main", cap = "samsungce.powerCool", attr = "activated",
        on = { cmd = "activate" }, off = { cmd = "deactivate" } },
      { comp = "main", cap = "refrigeration", attr = "rapidCooling",
        on = { cmd = "setRapidCooling", args = { "on" } }, off = { cmd = "setRapidCooling", args = { "off" } } },
    },
  },
  {
    key = "powerFreeze", name = "Power Freeze", binding = 5003, button = 301,
    var = "POWER_FREEZE", cond = "POWER_FREEZE",
    variants = {
      { comp = "main", cap = "samsungce.powerFreeze", attr = "activated",
        on = { cmd = "activate" }, off = { cmd = "deactivate" } },
      { comp = "main", cap = "refrigeration", attr = "rapidFreezing",
        on = { cmd = "setRapidFreezing", args = { "on" } }, off = { cmd = "setRapidFreezing", args = { "off" } } },
    },
  },
  {
    key = "sabbath", name = "Sabbath Mode", binding = 5004, button = 302,
    var = "SABBATH_MODE", cond = "SABBATH_MODE",
    variants = {
      { comp = "main", cap = "samsungce.sabbathMode", attr = "status",
        on = { cmd = "on" }, off = { cmd = "off" } },
    },
  },
  {
    key = "iceMaker", name = "Ice Maker", binding = 5005, button = 303,
    var = "ICE_MAKER", cond = "ICE_MAKER",
    variants = {
      { comp = "icemaker", cap = "switch", attr = "switch", on = { cmd = "on" }, off = { cmd = "off" } },
    },
  },
}

SETPOINTS = {
  { key = "fridge", name = "Fridge", comps = { "cooler", "onedoor" },
    prop = "Fridge Setpoint", tempProp = "Fridge Temperature", var = "FRIDGE_SETPOINT", tempVar = "FRIDGE_TEMP" },
  { key = "freezer", name = "Freezer", comps = { "freezer" },
    prop = "Freezer Setpoint", tempProp = "Freezer Temperature", var = "FREEZER_SETPOINT", tempVar = "FREEZER_TEMP" },
}

local DOOR_COMPONENTS = {
  { comp = "cooler", name = "Fridge" }, { comp = "freezer", name = "Freezer" },
  { comp = "cvroom", name = "FlexZone" }, { comp = "onedoor", name = "Door" },
}

local FEATURE_BY_KEY, FEATURE_BY_NAME, FEATURE_BY_BINDING = {}, {}, {}
for _, f in ipairs(FEATURES) do
  FEATURE_BY_KEY[f.key] = f
  FEATURE_BY_NAME[f.name] = f
  FEATURE_BY_BINDING[f.binding] = f
  FEATURE_BY_BINDING[f.button] = f
end
local SETPOINT_BY_KEY = {}
for _, sp in ipairs(SETPOINTS) do SETPOINT_BY_KEY[sp.key] = sp end

------------------------------------------------------------------------------
-- Runtime state
------------------------------------------------------------------------------
gDebug = false
gAuth = nil          -- { access_token, refresh_token, expires_at, client_id, scope, installed_app_id }
gDevices = {}        -- list label -> deviceId (from discovery)
gState = {
  online = nil,      -- true | false | nil
  authProblem = nil, -- string when re-authorization is required
  doorOpen = nil,    -- true | false | nil
  doorOpenSince = nil,
  doorAlerted = false,
  filterStatus = nil,
  statusRead = false,
  refreshing = false,  -- status tile tapped, refresh in progress
  refreshError = nil,  -- last status refresh error (status tile only)
}
gOps = {}            -- pending command confirmations, by key
local gSeq = 0
local gTimers = {}
local gRenewing = false
local gRenewWaiters = {}

------------------------------------------------------------------------------
-- Logging
------------------------------------------------------------------------------
local function log(...)
  local parts = {}
  for i = 1, select("#", ...) do parts[#parts + 1] = tostring((select(i, ...))) end
  print(os.date("%Y-%m-%d %H:%M:%S") .. " [SamsungFridge] " .. table.concat(parts, " "))
end

local function dbg(...)
  if gDebug then log(...) end
end

local function logError(msg)
  log("ERROR: " .. tostring(msg))
  if C4.ErrorLog then pcall(C4.ErrorLog, C4, "[DirectorLink Samsung Refrigerator] " .. tostring(msg)) end
end

------------------------------------------------------------------------------
-- Minimal JSON codec (C4:JsonEncode turns Lua arrays into objects by default
-- and C4:JsonDecode turns null into {}, so we bundle our own).
-- null decodes to nil.
------------------------------------------------------------------------------
JSON = {}

do
  local escapes = {
    ['"'] = '\\"', ['\\'] = '\\\\', ['\b'] = '\\b', ['\f'] = '\\f',
    ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t',
  }

  local function encodeString(s)
    return '"' .. (s:gsub('[%c"\\]', function(c)
      return escapes[c] or string.format("\\u%04x", c:byte())
    end)) .. '"'
  end

  local function isArray(t)
    local n = 0
    for k in pairs(t) do
      if type(k) ~= "number" or k < 1 or math.floor(k) ~= k then return false end
      if k > n then n = k end
    end
    return n > 0 and n == #t, n
  end

  local encodeValue

  function encodeValue(v, out)
    local tv = type(v)
    if v == nil then
      out[#out + 1] = "null"
    elseif tv == "boolean" then
      out[#out + 1] = v and "true" or "false"
    elseif tv == "number" then
      if v ~= v or v == math.huge or v == -math.huge then error("cannot encode non-finite number") end
      if math.floor(v) == v and math.abs(v) < 1e15 then
        out[#out + 1] = string.format("%d", v)
      else
        out[#out + 1] = string.format("%.17g", v)
      end
    elseif tv == "string" then
      out[#out + 1] = encodeString(v)
    elseif tv == "table" then
      local arr, n = isArray(v)
      if arr then
        out[#out + 1] = "["
        for i = 1, n do
          if i > 1 then out[#out + 1] = "," end
          encodeValue(v[i], out)
        end
        out[#out + 1] = "]"
      else
        local keys = {}
        for k in pairs(v) do keys[#keys + 1] = tostring(k) end
        table.sort(keys)
        out[#out + 1] = "{"
        for i, k in ipairs(keys) do
          if i > 1 then out[#out + 1] = "," end
          out[#out + 1] = encodeString(k)
          out[#out + 1] = ":"
          local val = v[k]
          if val == nil then val = v[tonumber(k)] end
          encodeValue(val, out)
        end
        out[#out + 1] = "}"
      end
    else
      error("cannot encode type " .. tv)
    end
  end

  function JSON.encode(v)
    local out = {}
    encodeValue(v, out)
    return table.concat(out)
  end

  local function utf8char(cp)
    if cp < 0x80 then
      return string.char(cp)
    elseif cp < 0x800 then
      return string.char(0xC0 + math.floor(cp / 0x40), 0x80 + cp % 0x40)
    elseif cp < 0x10000 then
      return string.char(0xE0 + math.floor(cp / 0x1000), 0x80 + math.floor(cp / 0x40) % 0x40, 0x80 + cp % 0x40)
    else
      return string.char(0xF0 + math.floor(cp / 0x40000), 0x80 + math.floor(cp / 0x1000) % 0x40,
        0x80 + math.floor(cp / 0x40) % 0x40, 0x80 + cp % 0x40)
    end
  end

  local simpleEscapes = { b = "\b", f = "\f", n = "\n", r = "\r", t = "\t", ['"'] = '"', ["\\"] = "\\", ["/"] = "/" }

  local function decodeError(pos, msg)
    error(string.format("JSON decode error at position %d: %s", pos, msg), 0)
  end

  local function skip(str, pos)
    return str:find("[^ \t\r\n]", pos) or (#str + 1)
  end

  local decodeValue

  local function decodeString(str, pos)
    local parts = {}
    local i = pos + 1
    while true do
      local s = str:find('["\\]', i)
      if not s then decodeError(pos, "unterminated string") end
      parts[#parts + 1] = str:sub(i, s - 1)
      if str:sub(s, s) == '"' then
        return table.concat(parts), s + 1
      end
      local esc = str:sub(s + 1, s + 1)
      if esc == "u" then
        local hex = str:sub(s + 2, s + 5)
        if not hex:match("^%x%x%x%x$") then decodeError(s, "bad unicode escape") end
        local cp = tonumber(hex, 16)
        local nextI = s + 6
        if cp >= 0xD800 and cp <= 0xDBFF and str:sub(nextI, nextI + 1) == "\\u" then
          local lo = tonumber(str:sub(nextI + 2, nextI + 5), 16)
          if lo and lo >= 0xDC00 and lo <= 0xDFFF then
            cp = 0x10000 + (cp - 0xD800) * 0x400 + (lo - 0xDC00)
            nextI = nextI + 6
          end
        end
        parts[#parts + 1] = utf8char(cp)
        i = nextI
      else
        local c = simpleEscapes[esc]
        if not c then decodeError(s, "bad escape") end
        parts[#parts + 1] = c
        i = s + 2
      end
    end
  end

  function decodeValue(str, pos)
    pos = skip(str, pos)
    local c = str:sub(pos, pos)
    if c == "{" then
      local obj = {}
      pos = skip(str, pos + 1)
      if str:sub(pos, pos) == "}" then return obj, pos + 1 end
      while true do
        pos = skip(str, pos)
        if str:sub(pos, pos) ~= '"' then decodeError(pos, "expected string key") end
        local key
        key, pos = decodeString(str, pos)
        pos = skip(str, pos)
        if str:sub(pos, pos) ~= ":" then decodeError(pos, "expected ':'") end
        local val
        val, pos = decodeValue(str, pos + 1)
        obj[key] = val
        pos = skip(str, pos)
        local d = str:sub(pos, pos)
        if d == "}" then return obj, pos + 1 end
        if d ~= "," then decodeError(pos, "expected ',' or '}'") end
        pos = pos + 1
      end
    elseif c == "[" then
      local arr, n = {}, 0
      pos = skip(str, pos + 1)
      if str:sub(pos, pos) == "]" then return arr, pos + 1 end
      while true do
        local val
        val, pos = decodeValue(str, pos)
        n = n + 1
        arr[n] = val
        pos = skip(str, pos)
        local d = str:sub(pos, pos)
        if d == "]" then return arr, pos + 1 end
        if d ~= "," then decodeError(pos, "expected ',' or ']'") end
        pos = pos + 1
      end
    elseif c == '"' then
      return decodeString(str, pos)
    elseif c == "-" or c:match("%d") then
      local num = str:match("^-?%d+%.?%d*[eE]?[-+]?%d*", pos)
      local v = tonumber(num)
      if not v then decodeError(pos, "bad number") end
      return v, pos + #num
    elseif str:sub(pos, pos + 3) == "true" then
      return true, pos + 4
    elseif str:sub(pos, pos + 4) == "false" then
      return false, pos + 5
    elseif str:sub(pos, pos + 3) == "null" then
      return nil, pos + 4
    end
    decodeError(pos, "unexpected character '" .. c .. "'")
  end

  function JSON.decode(str)
    if type(str) ~= "string" then error("JSON.decode expects a string", 0) end
    local v, pos = decodeValue(str, 1)
    pos = skip(str, pos)
    if pos <= #str then decodeError(pos, "trailing garbage") end
    return v
  end
end

------------------------------------------------------------------------------
-- Small helpers
------------------------------------------------------------------------------
local function trim(s)
  return (tostring(s or ""):gsub("^%s+", ""):gsub("%s+$", ""))
end

local function urlEncode(s)
  return (tostring(s):gsub("[^%w%-%._~]", function(c) return string.format("%%%02X", c:byte()) end))
end

local function urlDecode(s)
  s = tostring(s):gsub("%+", " ")
  return (s:gsub("%%(%x%x)", function(h) return string.char(tonumber(h, 16)) end))
end

-- pairsList is a list of {key, value} so the output order is stable
local function formEncode(pairsList)
  local out = {}
  for _, kv in ipairs(pairsList) do
    out[#out + 1] = urlEncode(kv[1]) .. "=" .. urlEncode(kv[2])
  end
  return table.concat(out, "&")
end

local function dig(t, ...)
  for i = 1, select("#", ...) do
    if type(t) ~= "table" then return nil end
    t = t[(select(i, ...))]
  end
  return t
end

local function listHas(list, value)
  if type(list) ~= "table" then return false end
  for _, v in ipairs(list) do
    if v == value then return true end
  end
  return false
end

local function fmtTime(ts)
  return os.date("%Y-%m-%d %H:%M", ts)
end

local function fmtNumber(v)
  if type(v) ~= "number" then return tostring(v) end
  if math.floor(v) == v then return string.format("%d", v) end
  return string.format("%.1f", v)
end

local function fmtTemp(value, unit)
  if value == nil then return "" end
  return fmtNumber(value) .. ((unit and unit ~= "") and (" \194\176" .. unit) or "")
end

local function randomHex(n)
  local out = {}
  for i = 1, n do out[i] = string.format("%x", math.random(0, 15)) end
  return table.concat(out)
end

local function setProp(name, value)
  value = tostring(value == nil and "" or value)
  if Properties[name] ~= value then C4:UpdateProperty(name, value) end
end

local function setVar(name, value)
  pcall(C4.SetVariable, C4, name, tostring(value))
end

local function fireEvent(name)
  dbg("Firing event:", name)
  pcall(C4.FireEvent, C4, name)
end

local function cancelTimer(name)
  local t = gTimers[name]
  if t then
    pcall(function() t:Cancel() end)
    gTimers[name] = nil
  end
end

local function setTimer(name, ms, fn, rep)
  cancelTimer(name)
  gTimers[name] = C4:SetTimer(ms, function(timer, skips)
    if not rep then gTimers[name] = nil end
    local ok, err = pcall(fn, timer, skips)
    if not ok then logError("timer " .. name .. ": " .. tostring(err)) end
  end, rep and true or false)
end

local function persistGet(name, encrypted)
  local ok, v = pcall(C4.PersistGetValue, C4, name, encrypted)
  if ok then return v end
  return nil
end

local function persistSet(name, value, encrypted)
  local ok, err = pcall(C4.PersistSetValue, C4, name, value, encrypted)
  if not ok then logError("PersistSetValue(" .. name .. ") failed: " .. tostring(err)) end
end

local function persistDelete(name)
  if C4.PersistDeleteValue then pcall(C4.PersistDeleteValue, C4, name) end
end

------------------------------------------------------------------------------
-- Presentation (tiles, keypad LEDs, properties, variables)
------------------------------------------------------------------------------
local function globalProblem()
  if gState.authProblem then return "Authorization required" end
  if gState.online == false then return "Refrigerator offline" end
  return nil
end

local function featureText(f)
  if f.pending then return f.pending.target and "Turning On..." or "Turning Off..." end
  if f.supported == false then return "Not available" end
  if f.state == true then return "On" end
  if f.state == false then return "Off" end
  return "Unknown"
end

function UpdateFeatureUI(f)
  local icon
  local problem = globalProblem()
  if f.pending then
    icon = "pending"
  elseif f.flash or (problem and f.supported ~= false) then
    icon = "error"
  elseif f.supported == false then
    icon = "unavailable"
  elseif f.state == true then
    icon = "on"
  else
    icon = "off"
  end
  local desc = f.name .. ": " .. ((icon == "error" and (problem or "command failed")) or featureText(f))
  if icon ~= f.lastIcon or desc ~= f.lastDesc then
    pcall(C4.SendToProxy, C4, f.binding, "ICON_CHANGED", { icon = icon, icon_description = desc })
    f.lastIcon, f.lastDesc = icon, desc
  end
  local led = (f.state == true) and "1" or "0"
  if led ~= f.lastLed then
    pcall(C4.SendToProxy, C4, f.button, "MATCH_LED_STATE", { STATE = led })
    f.lastLed = led
  end
  setProp(f.name, featureText(f))
  setVar(f.var, f.state == true and "1" or "0")
end

-- The "Samsung Refrigerator" tile: overall state at a glance; tapping it refreshes.
function UpdateStatusTile()
  local icon, desc
  local problem = globalProblem() or gState.refreshError
  local temps = {}
  if (Properties["Fridge Temperature"] or "") ~= "" and Properties["Fridge Temperature"] ~= "Not available" then
    temps[#temps + 1] = "Fridge " .. Properties["Fridge Temperature"]
  end
  if (Properties["Freezer Temperature"] or "") ~= "" and Properties["Freezer Temperature"] ~= "Not available" then
    temps[#temps + 1] = "Freezer " .. Properties["Freezer Temperature"]
  end
  temps = table.concat(temps, ", ")
  if gState.refreshing then
    icon, desc = "pending", "Refrigerator: refreshing..."
  elseif problem then
    icon, desc = "error", "Refrigerator: " .. problem
  elseif not bearerToken() or deviceId() == "" then
    icon, desc = "unknown", "Refrigerator: not set up"
  elseif not gState.statusRead then
    icon, desc = "unknown", "Refrigerator: waiting for status"
  elseif gState.doorOpen then
    icon, desc = "door", "Refrigerator: " .. tostring(Properties["Doors"])
  else
    icon, desc = "ok", "Refrigerator: OK" .. (temps ~= "" and (" - " .. temps) or "")
  end
  if icon ~= gState.statusIcon or desc ~= gState.statusDesc then
    pcall(C4.SendToProxy, C4, STATUS_BINDING, "ICON_CHANGED", { icon = icon, icon_description = desc })
    gState.statusIcon, gState.statusDesc = icon, desc
  end
end

function UpdateUI()
  for _, f in ipairs(FEATURES) do UpdateFeatureUI(f) end
  UpdateStatusTile()
end

local function setDriverStatus(text)
  setProp("Driver Status", text)
end

local function updateAuthStatus()
  local text
  if gState.authProblem then
    text = "Re-authorization required: " .. gState.authProblem
  elseif gAuth and gAuth.access_token then
    if gAuth.refresh_token then
      text = "Authorized (OAuth) - access token valid until " .. fmtTime(gAuth.expires_at or 0) ..
        ", renews automatically"
    else
      text = "Authorized (no refresh token) - expires " .. fmtTime(gAuth.expires_at or 0)
    end
  elseif trim(Properties["Personal Access Token"]) ~= "" then
    text = "Using Personal Access Token only (new PATs expire after 24h - finish OAuth setup)"
  else
    text = "Not authorized"
  end
  setProp("Authorization Status", text)
end

local function flashError(f)
  if not f then return end   -- only feature tiles flash
  f.flash = true
  UpdateFeatureUI(f)
  setTimer("flash", ERROR_FLASH_MS, function()
    for _, x in ipairs(FEATURES) do x.flash = false end
    UpdateUI()
  end)
end

local function commandFailed(msg, f)
  logError(msg)
  setDriverStatus("Command failed: " .. msg)
  fireEvent("Command Failed")
  flashError(f)
end

------------------------------------------------------------------------------
-- HTTP
------------------------------------------------------------------------------
-- cb(errCode, errMsg, httpCode, body)  errCode 0 = transfer completed
-- sensitive = true keeps the response body (tokens, secrets) out of the log
local function httpRequest(method, url, data, headers, cb, sensitive)
  dbg(">>", method, url, data and not sensitive and data or "")
  local ok, err = pcall(function()
    local t = C4:url()
    t:SetOptions({ fail_on_error = false, timeout = 30, connect_timeout = 10 })
    t:OnDone(function(transfer, responses, errCode, errMsg)
      local r = responses and responses[#responses]
      local code = r and r.code
      local body = r and r.body or ""
      dbg("<<", method, url, "err=" .. tostring(errCode), "http=" .. tostring(code),
        (gDebug and not sensitive and #body > 0) and body:sub(1, 600) or "")
      local cbOk, cbErr = pcall(cb, errCode or -2, errMsg, code, body)
      if not cbOk then logError("HTTP callback failed: " .. tostring(cbErr)) end
    end)
    if method == "GET" then
      t:Get(url, headers)
    elseif method == "POST" then
      t:Post(url, data or "", headers)
    elseif method == "PUT" then
      t:Put(url, data or "", headers)
    elseif method == "DELETE" then
      t:Delete(url, headers)
    else
      t:Custom(url, method, data or "", headers)
    end
  end)
  if not ok then
    logError("HTTP request could not be started: " .. tostring(err))
    pcall(cb, -3, tostring(err), nil, "")
  end
end

local function safeDecode(body)
  if not body or body == "" then return nil end
  local ok, v = pcall(JSON.decode, body)
  if ok then return v end
  dbg("Response is not JSON:", body:sub(1, 200))
  return nil
end

local function describeHttpError(code, decoded)
  local msg = dig(decoded, "error", "message") or dig(decoded, "error_description")
    or dig(decoded, "message") or (type(dig(decoded, "error")) == "string" and decoded.error)
  local base = ({
    [400] = "Bad request", [401] = "Not authorized", [403] = "Forbidden (missing permission/scope)",
    [404] = "Not found (check Device ID)", [409] = "Conflict", [422] = "Rejected by the refrigerator",
    [429] = "Rate limited by SmartThings", [500] = "SmartThings server error",
    [503] = "SmartThings unavailable",
  })[code] or ("HTTP " .. tostring(code))
  if msg and msg ~= "" then return base .. ": " .. tostring(msg) end
  return base
end

------------------------------------------------------------------------------
-- OAuth
------------------------------------------------------------------------------
local function oauthClient()
  return trim(Properties["OAuth Client ID"]), trim(Properties["OAuth Client Secret"]),
    trim(Properties["OAuth Redirect URI"])
end

local function saveTokens(resp, clientId)
  local refresh = resp.refresh_token
  if (refresh == nil or refresh == "") and gAuth then refresh = gAuth.refresh_token end
  gAuth = {
    access_token = resp.access_token,
    refresh_token = refresh,
    expires_at = os.time() + (tonumber(resp.expires_in) or 86400),
    client_id = clientId,
    scope = resp.scope,
    installed_app_id = resp.installed_app_id,
  }
  -- Persist immediately: the previous refresh token is now consumed.
  persistSet(PERSIST_AUTH, gAuth, true)
  gState.authProblem = nil
  updateAuthStatus()
end

local function clearTokens(reason)
  gAuth = nil
  persistDelete(PERSIST_AUTH)
  persistSet(PERSIST_AUTH, {}, true)
  if reason then
    gState.authProblem = reason
    fireEvent("Authorization Required")
  end
  updateAuthStatus()
  UpdateUI()
end

local function tokenRequest(bodyPairs, cb)
  local clientId, secret = oauthClient()
  if clientId == "" or secret == "" then
    cb(false, "OAuth Client ID / Secret not set", false)
    return
  end
  local headers = {
    ["Authorization"] = "Basic " .. C4:Base64Encode(clientId .. ":" .. secret),
    ["Content-Type"] = "application/x-www-form-urlencoded",
    ["Accept"] = "application/json",
    ["User-Agent"] = USER_AGENT,
  }
  httpRequest("POST", TOKEN_URL, formEncode(bodyPairs), headers, function(errCode, errMsg, code, body)
    if errCode ~= 0 or not code then
      cb(false, "Network error: " .. tostring(errMsg), true)
      return
    end
    local decoded = safeDecode(body)
    if code == 200 and type(decoded) == "table" and decoded.access_token then
      saveTokens(decoded, clientId)
      cb(true)
    else
      local transient = (code >= 500 or code == 429)
      cb(false, describeHttpError(code, decoded), transient)
    end
  end, true)
end

local function finishRenew(ok, err)
  gRenewing = false
  local waiters = gRenewWaiters
  gRenewWaiters = {}
  for _, w in ipairs(waiters) do pcall(w, ok, err) end
end

-- Renew the access token. Concurrent callers share one request, because a
-- refresh token can only be used once.
function RenewToken(cb)
  if cb then gRenewWaiters[#gRenewWaiters + 1] = cb end
  if gRenewing then return end
  if not (gAuth and gAuth.refresh_token) then
    if not gState.authProblem then
      setProp("Authorization Status", "Cannot renew - not authorized with OAuth yet")
    end
    finishRenew(false, "No refresh token - authorize first")
    return
  end
  gRenewing = true
  local clientId = oauthClient()
  log("Renewing SmartThings access token")
  tokenRequest({
    { "grant_type", "refresh_token" },
    { "client_id", clientId },
    { "refresh_token", gAuth.refresh_token },
  }, function(ok, err, transient)
    if ok then
      log("Access token renewed, valid until " .. fmtTime(gAuth.expires_at))
      cancelTimer("renewRetry")
    elseif transient then
      logError("Token renewal failed (will retry): " .. tostring(err))
      setProp("Authorization Status", "Token renewal failed, retrying: " .. tostring(err))
      setTimer("renewRetry", RENEW_RETRY_MS, function() RenewToken() end)
    else
      logError("Token renewal rejected: " .. tostring(err))
      clearTokens("token renewal rejected (" .. tostring(err) .. ")")
    end
    finishRenew(ok, err)
  end)
end

local function maybeRenewToken(cb)
  if gAuth and gAuth.refresh_token and (gAuth.expires_at or 0) - os.time() < RENEW_MARGIN_S then
    RenewToken(cb)
  elseif cb then
    cb(true)
  end
end

local function extractAuthCode(text)
  text = trim(text)
  local code = text:match("[?&]code=([^&#%s\"']+)") or text:match("^code=([^&#%s\"']+)")
    or text:match('"code"%s*:%s*"([^"]+)"')
  if not code and not text:find("[%s/?&=]") then code = text end
  return code and urlDecode(code) or nil
end

local function buildAuthUrl()
  local clientId, _, redirect = oauthClient()
  if clientId == "" or redirect == "" then return "" end
  return AUTHORIZE_URL .. "?client_id=" .. urlEncode(clientId) .. "&response_type=code" ..
    "&redirect_uri=" .. urlEncode(redirect) .. "&scope=" .. urlEncode(table.concat(OAUTH_SCOPES, " "))
end

local function updateAuthUrl()
  setProp("Authorization URL", buildAuthUrl())
end

------------------------------------------------------------------------------
-- SmartThings API
------------------------------------------------------------------------------
function bearerToken()
  if gAuth and gAuth.access_token then return gAuth.access_token, "oauth" end
  local pat = trim(Properties["Personal Access Token"])
  if pat ~= "" then return pat, "pat" end
  return nil
end

-- pathOrUrl: "/devices/..." or an absolute https URL (pagination links)
-- cb(ok, httpCode, decodedBody, errorText)
local function apiRequest(method, pathOrUrl, body, cb, isRetry)
  local token, kind = bearerToken()
  if not token then
    cb(false, nil, nil, "Not authorized - complete the SmartThings setup")
    return
  end
  if kind == "oauth" and not isRetry and gAuth.refresh_token and os.time() >= (gAuth.expires_at or 0) - 60 then
    RenewToken(function(ok, err)
      if ok then apiRequest(method, pathOrUrl, body, cb, true) else cb(false, 401, nil, err) end
    end)
    return
  end
  local headers = {
    ["Authorization"] = "Bearer " .. token,
    ["Accept"] = "application/json",
    ["User-Agent"] = USER_AGENT,
  }
  local data
  if body ~= nil then
    data = JSON.encode(body)
    headers["Content-Type"] = "application/json"
  end
  local url = pathOrUrl:find("^https://") and pathOrUrl or (API_BASE .. pathOrUrl)
  httpRequest(method, url, data, headers, function(errCode, errMsg, code, respBody)
    if errCode ~= 0 or not code then
      cb(false, nil, nil, "Network error: " .. tostring(errMsg))
      return
    end
    if code == 401 and kind == "oauth" and not isRetry then
      if gAuth and gAuth.access_token ~= token then
        apiRequest(method, pathOrUrl, body, cb, true)     -- token was renewed meanwhile
      elseif gAuth and gAuth.refresh_token then
        RenewToken(function(ok, err)
          if ok then apiRequest(method, pathOrUrl, body, cb, true) else cb(false, 401, nil, err) end
        end)
      else
        cb(false, 401, nil, "Not authorized")
      end
      return
    end
    local decoded = safeDecode(respBody)
    if code >= 200 and code < 300 then
      cb(true, code, decoded)
    else
      cb(false, code, decoded, describeHttpError(code, decoded))
    end
  end)
end

function deviceId()
  return trim(Properties["Device ID"])
end

local function devicePath(suffix)
  return "/devices/" .. urlEncode(deviceId()) .. suffix
end

-- cb(ok, httpCode, errorText)
local function sendCommand(comp, cap, cmd, args, cb)
  local c = { component = comp, capability = cap, command = cmd }
  if args and #args > 0 then c.arguments = args end
  log("Command: " .. comp .. " " .. cap .. "." .. cmd .. (args and #args > 0 and ("(" .. tostring(args[1]) .. ")") or ""))
  apiRequest("POST", devicePath("/commands"), { commands = { c } }, function(ok, code, data, err)
    if ok and dig(data, "results", 1, "status") == "FAILED" then
      ok, err = false, "SmartThings reported the command as FAILED"
    end
    cb(ok, code, err)
  end)
end

------------------------------------------------------------------------------
-- Status parsing (pure function: SmartThings /status JSON -> summary)
------------------------------------------------------------------------------
local function componentAvailable(comps, comp)
  if type(comps[comp]) ~= "table" then return false, "not present" end
  if comp ~= "main" and listHas(dig(comps, "main", "custom.disabledComponents", "disabledComponents", "value"), comp) then
    return false, "disabled for this model"
  end
  return true
end

local function capabilityAvailable(comps, comp, cap)
  local ok, why = componentAvailable(comps, comp)
  if not ok then return false, why end
  if type(comps[comp][cap]) ~= "table" then return false, "not supported by this model" end
  if listHas(dig(comps, comp, "custom.disabledCapabilities", "disabledCapabilities", "value"), cap) then
    return false, "disabled for this model"
  end
  return true
end

local function isOn(v)
  return v == true or v == "on" or v == "true" or v == "True"
end

local function prettyMode(mode)
  if type(mode) ~= "string" then return nil end
  local known = {
    CV_TTYPE_FREEZER = "Freezer", CV_TTYPE_SOFT_FREEZER = "Soft Freeze",
    CV_TTYPE_CHILL = "Chill", CV_TTYPE_COOL = "Cool", CV_TTYPE_WINE = "Wine",
    CV_TTYPE_MEAT_FISH = "Meat & Fish", CV_TTYPE_FRUIT_VEG = "Fruit & Veg",
  }
  if known[mode] then return known[mode] end
  local s = mode:gsub("^.*TTYPE_", ""):gsub("_", " "):lower()
  return (s:gsub("(%a)([%w]*)", function(a, b) return a:upper() .. b end))
end

function ParseDeviceStatus(st)
  local comps = dig(st, "components") or {}
  local main = comps.main or {}
  local res = { features = {}, setpoints = {}, doors = {} }

  for _, f in ipairs(FEATURES) do
    local r = { supported = false, reason = "not supported by this model" }
    for i, v in ipairs(f.variants) do
      local ok, why = capabilityAvailable(comps, v.comp, v.cap)
      local val = ok and dig(comps, v.comp, v.cap, v.attr, "value")
      if ok and val ~= nil then
        r = { supported = true, on = isOn(val), variant = i }
        break
      elseif i == 1 then
        r.reason = ok and "no value reported" or why
      end
    end
    res.features[f.key] = r
  end

  for _, sp in ipairs(SETPOINTS) do
    local r = { supported = false }
    for _, comp in ipairs(sp.comps) do
      if componentAvailable(comps, comp) then
        local temp = capabilityAvailable(comps, comp, "temperatureMeasurement")
          and dig(comps, comp, "temperatureMeasurement", "temperature")
        local set = capabilityAvailable(comps, comp, "thermostatCoolingSetpoint")
          and dig(comps, comp, "thermostatCoolingSetpoint", "coolingSetpoint")
        if (temp and temp.value ~= nil) or (set and set.value ~= nil) then
          r.comp = comp
          if temp and temp.value ~= nil then r.temp, r.tempUnit = temp.value, temp.unit end
          if set and set.value ~= nil then
            r.supported, r.value, r.unit = true, set.value, set.unit or r.tempUnit
            local range = dig(comps, comp, "thermostatCoolingSetpoint", "coolingSetpointRange", "value")
            if type(range) == "table" and range.minimum then
              r.min, r.max, r.step = range.minimum, range.maximum, range.step
            else
              r.min = dig(comps, comp, "custom.thermostatSetpointControl", "minimumSetpoint", "value")
              r.max = dig(comps, comp, "custom.thermostatSetpointControl", "maximumSetpoint", "value")
            end
            r.step = tonumber(r.step) or 1
            if r.step <= 0 then r.step = 1 end
          end
          r.unit = r.unit or r.tempUnit
          break
        end
      end
    end
    res.setpoints[sp.key] = r
  end

  if capabilityAvailable(comps, "cvroom", "custom.fridgeMode") then
    res.flexZone = prettyMode(dig(comps, "cvroom", "custom.fridgeMode", "fridgeMode", "value"))
  end
  if capabilityAvailable(comps, "cvroom", "temperatureMeasurement") then
    local t = dig(comps, "cvroom", "temperatureMeasurement", "temperature")
    if t and t.value ~= nil then res.flexZoneTemp = fmtTemp(t.value, t.unit) end
  end

  for _, d in ipairs(DOOR_COMPONENTS) do
    if capabilityAvailable(comps, d.comp, "contactSensor") then
      local v = dig(comps, d.comp, "contactSensor", "contact", "value")
      if v == "open" or v == "closed" then res.doors[#res.doors + 1] = { name = d.name, open = (v == "open") } end
    end
  end
  if #res.doors == 0 and capabilityAvailable(comps, "main", "contactSensor") then
    local v = dig(main, "contactSensor", "contact", "value")
    if v == "open" or v == "closed" then res.doors[1] = { name = "Door", open = (v == "open") } end
  end

  if capabilityAvailable(comps, "main", "custom.waterFilter") then
    local status = dig(main, "custom.waterFilter", "waterFilterStatus", "value")
    local usage = dig(main, "custom.waterFilter", "waterFilterUsage", "value")
    if status ~= nil or usage ~= nil then res.filter = { status = status, usage = usage } end
  end

  if capabilityAvailable(comps, "main", "powerConsumptionReport") then
    local pc = dig(main, "powerConsumptionReport", "powerConsumption", "value")
    if type(pc) == "table" then
      res.powerW = tonumber(pc.power)
      if tonumber(pc.energy) then res.energyKWh = tonumber(pc.energy) / 1000 end
    end
  end

  local mnmo = dig(main, "ocf", "mnmo", "value")
  if type(mnmo) == "string" then res.model = mnmo:match("^([^|]+)") end
  res.firmware = dig(main, "ocf", "mnfv", "value")
  return res
end

------------------------------------------------------------------------------
-- Applying status
------------------------------------------------------------------------------
local function persistLastStates()
  local t = {}
  for _, f in ipairs(FEATURES) do
    if f.state ~= nil then t[f.key] = f.state end
  end
  persistSet(PERSIST_LAST, t, false)
end

local function setpointItems(r)
  if not (r.min and r.max) then return nil end
  local items = {}
  local v = r.min
  local guard = 0
  while v <= r.max + 1e-9 and guard < 200 do
    items[#items + 1] = fmtTemp(v, r.unit)
    v = v + r.step
    guard = guard + 1
  end
  return items
end

-- Push the setpoint dropdown (allowed values + current value) to Composer
local function publishSetpoint(sp, force)
  if gOps["setpoint:" .. sp.key] then return end   -- keep the user's selection while confirming
  if force then sp.lastList = nil end
  if sp.supported then
    local items = setpointItems(sp) or {}
    local current = fmtTemp(sp.value, sp.unit)
    if not listHas(items, current) then items[#items + 1] = current end
    local list = table.concat(items, ",")
    if list ~= sp.lastList or Properties[sp.prop] ~= current then
      C4:UpdatePropertyList(sp.prop, list, current)
      sp.lastList = list
    end
  elseif sp.lastList ~= "Not available" then
    C4:UpdatePropertyList(sp.prop, "Not available", "Not available")
    sp.lastList = "Not available"
  end
end

local function applySetpoint(sp, r)
  local oldValue = sp.value
  sp.supported, sp.comp, sp.unit = r.supported, r.comp, r.unit
  sp.min, sp.max, sp.step = r.min, r.max, r.step
  sp.value = r.value
  setProp(sp.tempProp, r.temp ~= nil and fmtTemp(r.temp, r.tempUnit) or (r.comp and "" or "Not available"))
  if r.temp ~= nil then setVar(sp.tempVar, fmtNumber(r.temp)) end
  if r.supported then
    setVar(sp.var, fmtNumber(r.value))
    if oldValue ~= nil and oldValue ~= r.value then
      log(sp.name .. " setpoint changed: " .. fmtNumber(oldValue) .. " -> " .. fmtNumber(r.value))
      fireEvent("Setpoint Changed")
    end
  end
  publishSetpoint(sp)
end

local function applyDoors(doors)
  if #doors == 0 then
    setProp("Doors", "Not available")
    return
  end
  local open = {}
  for _, d in ipairs(doors) do
    if d.open then open[#open + 1] = d.name end
  end
  local anyOpen = #open > 0
  setProp("Doors", anyOpen and ("Open: " .. table.concat(open, ", ")) or "Closed")
  setVar("DOOR_OPEN", anyOpen and "1" or "0")
  local was = gState.doorOpen
  gState.doorOpen = anyOpen
  if anyOpen then
    if was == false then
      fireEvent("Door Opened")
    end
    if not gState.doorOpenSince then gState.doorOpenSince = os.time() end
    local alertMin = tonumber(Properties["Door Open Alert (minutes)"]) or 0
    if alertMin > 0 and not gState.doorAlerted and os.time() - gState.doorOpenSince >= alertMin * 60 then
      gState.doorAlerted = true
      log("Door left open: " .. table.concat(open, ", "))
      fireEvent("Door Left Open")
    end
  else
    if was == true then fireEvent("Door Closed") end
    gState.doorOpenSince, gState.doorAlerted = nil, false
  end
end

local function applyFilter(filter)
  if not filter then
    setProp("Water Filter", "Not available")
    return
  end
  local status = filter.status
  local replace = (status == "replace" or status == "Replace")
  local text = replace and "Replace" or "OK"
  if filter.usage ~= nil then
    text = text .. " (" .. fmtNumber(filter.usage) .. "% used)"
    setVar("WATER_FILTER_USAGE", fmtNumber(filter.usage))
  end
  setProp("Water Filter", text)
  local was = gState.filterStatus
  gState.filterStatus = replace and "Replace" or "OK"
  if replace and was == "OK" then fireEvent("Water Filter Needs Replacement") end
end

local function supportedFeatureList(res)
  local names = {}
  for _, f in ipairs(FEATURES) do
    if res.features[f.key].supported then names[#names + 1] = f.name end
  end
  for _, sp in ipairs(SETPOINTS) do
    if res.setpoints[sp.key].supported then names[#names + 1] = sp.name .. " setpoint" end
  end
  if res.filter then names[#names + 1] = "Water filter" end
  if #res.doors > 0 then names[#names + 1] = "Doors" end
  if res.powerW then names[#names + 1] = "Power" end
  return #names > 0 and table.concat(names, ", ") or "None"
end

function ApplyStatus(res)
  local changed = false
  for _, f in ipairs(FEATURES) do
    local r = res.features[f.key]
    local old = f.state
    f.supported, f.reason, f.variantIndex = r.supported, r.reason, r.variant
    if r.supported then
      f.state = r.on
      if old ~= nil and old ~= r.on then
        log(f.name .. " changed: " .. (r.on and "On" or "Off"))
        fireEvent(f.name .. " Turned " .. (r.on and "On" or "Off"))
      end
      if old ~= r.on then changed = true end
    else
      f.state = nil
      if old ~= nil then changed = true end
    end
  end
  if changed then persistLastStates() end

  for _, sp in ipairs(SETPOINTS) do applySetpoint(sp, res.setpoints[sp.key]) end
  setProp("FlexZone", res.flexZone and (res.flexZone .. (res.flexZoneTemp and (" (" .. res.flexZoneTemp .. ")") or ""))
    or "Not available")
  applyDoors(res.doors)
  applyFilter(res.filter)
  if res.powerW then
    setProp("Power", fmtNumber(res.powerW) .. " W")
    setVar("POWER_W", fmtNumber(res.powerW))
  else
    setProp("Power", "Not available")
  end
  setProp("Energy", res.energyKWh and (string.format("%.1f", res.energyKWh) .. " kWh total") or "Not available")
  if res.model then setProp("Model", res.model .. (res.firmware and (" (" .. res.firmware .. ")") or "")) end
  setProp("Supported Features", supportedFeatureList(res))
  setProp("Last Update", os.date("%Y-%m-%d %H:%M:%S"))
  gState.statusRead = true
end

local function applyHealth(online)
  local old = gState.online
  gState.online = online
  setProp("Connection", online and "Online" or "Offline")
  setVar("ONLINE", online and "1" or "0")
  if old ~= nil and old ~= online then
    log("Refrigerator is now " .. (online and "online" or "offline"))
    fireEvent(online and "Refrigerator Online" or "Refrigerator Offline")
  end
end

local function refreshSummary()
  if gState.authProblem then
    setDriverStatus("Re-authorization required")
  elseif gState.online == false then
    setDriverStatus("Refrigerator offline")
  elseif gState.statusRead then
    setDriverStatus("OK")
  end
end

-- cb(ok) optional. skipHealth: status only (used for command confirmation, to
-- stay well inside SmartThings' per-device rate limit of ~12 reads/minute).
function RefreshStatus(cb, skipHealth)
  if deviceId() == "" then
    setDriverStatus("No refrigerator selected - run Discover Refrigerators")
    if cb then cb(false) end
    return
  end
  apiRequest("GET", devicePath("/status"), nil, function(ok, code, data, err)
    if ok and type(data) == "table" then
      local parsedOk, res = pcall(ParseDeviceStatus, data)
      if not parsedOk then
        logError("Could not parse status: " .. tostring(res))
        if cb then cb(false) end
        return
      end
      ApplyStatus(res)
      gState.refreshError = nil
      if skipHealth then
        refreshSummary()
        UpdateUI()
        if cb then cb(true) end
        return
      end
      apiRequest("GET", devicePath("/health"), nil, function(hok, _, hdata)
        local state = hok and dig(hdata, "state")
        if state == "ONLINE" then
          applyHealth(true)
        elseif state == "OFFLINE" then
          applyHealth(false)
        end
        refreshSummary()
        UpdateUI()
        if cb then cb(true) end
      end)
    else
      logError("Status refresh failed: " .. tostring(err))
      setDriverStatus("Status refresh failed: " .. tostring(err))
      gState.refreshError = "status refresh failed"
      UpdateUI()
      if cb then cb(false, err) end
    end
  end)
end

------------------------------------------------------------------------------
-- Command confirmation: after SmartThings accepts a command, re-read the
-- status until the refrigerator reports the new value (HTTP 200 alone does
-- not mean the appliance changed).
------------------------------------------------------------------------------
local runConfirm

local function scheduleConfirm()
  local nextDue
  for _, op in pairs(gOps) do
    if not nextDue or op.due < nextDue then nextDue = op.due end
  end
  if not nextDue then
    cancelTimer("confirm")
    return
  end
  local delay = math.max(1, nextDue - os.time())
  setTimer("confirm", delay * 1000, runConfirm)
end

function runConfirm()
  RefreshStatus(function()
    local now = os.time()
    for key, op in pairs(gOps) do
      if op.check() then
        gOps[key] = nil
        log(op.label .. " confirmed by the refrigerator")
        op.settle(true)
      elseif now >= op.due then
        op.attempt = op.attempt + 1
        if op.attempt > #CONFIRM_DELAYS_S then
          gOps[key] = nil
          op.settle(false)
          commandFailed(op.label .. " was not confirmed by the refrigerator", op.feature)
        else
          op.due = now + CONFIRM_DELAYS_S[op.attempt]
        end
      end
    end
    UpdateUI()
    scheduleConfirm()
  end, true)
end

local function startConfirm(key, label, check, settle, feature)
  gOps[key] = { label = label, check = check, settle = settle, feature = feature,
    attempt = 1, due = os.time() + CONFIRM_DELAYS_S[1] }
  scheduleConfirm()
end

------------------------------------------------------------------------------
-- Feature commands
------------------------------------------------------------------------------
local function preflight(f)
  if deviceId() == "" then
    commandFailed("No refrigerator selected", f)
    return false
  end
  if not bearerToken() then
    commandFailed("Not authorized - complete the SmartThings setup", f)
    return false
  end
  return true
end

function SetFeature(key, target)
  local f = FEATURE_BY_KEY[key]
  if not f then return end
  target = (target == true or target == "on" or target == "On")
  if not preflight(f) then return end
  if not gState.statusRead then
    RefreshStatus(function(ok)
      if ok then SetFeature(key, target) else commandFailed("Cannot read refrigerator status", f) end
    end)
    return
  end
  if f.supported == false then
    commandFailed(f.name .. " is not available on this refrigerator (" .. tostring(f.reason) .. ")", f)
    return
  end
  local v = f.variants[f.variantIndex or 1]
  local c = target and v.on or v.off
  gSeq = gSeq + 1
  local seq = gSeq
  f.pending = { target = target, seq = seq }
  f.flash = false
  UpdateFeatureUI(f)

  sendCommand(v.comp, v.cap, c.cmd, c.args, function(ok, code, err)
    if not (f.pending and f.pending.seq == seq) then return end   -- superseded
    if not ok then
      f.pending = nil
      UpdateFeatureUI(f)
      commandFailed(f.name .. ": " .. tostring(err), f)
      return
    end
    startConfirm("feature:" .. f.key, f.name .. " " .. (target and "On" or "Off"),
      function() return f.state == target end,
      function()
        if f.pending and f.pending.seq == seq then f.pending = nil end
        UpdateFeatureUI(f)
      end, f)
  end)
end

function ToggleFeature(key)
  local f = FEATURE_BY_KEY[key]
  if not f then return end
  if f.pending then
    SetFeature(key, not f.pending.target)
  elseif f.supported == false then
    SetFeature(key, true)   -- reports why it is not available
  elseif f.state == nil or not gState.statusRead then
    if not preflight(f) then return end
    RefreshStatus(function(ok)
      if ok and (f.state ~= nil or f.supported == false) then
        SetFeature(key, not f.state)
      else
        commandFailed("Cannot toggle " .. f.name .. " - current state is unknown", f)
      end
    end)
  else
    SetFeature(key, not f.state)
  end
end

function SetSetpoint(key, value)
  local sp = SETPOINT_BY_KEY[key]
  value = tonumber(value)
  if not sp or not value then return end
  if not preflight(nil) then return end
  if not gState.statusRead then
    RefreshStatus(function(ok)
      if ok then SetSetpoint(key, value) else commandFailed("Cannot read refrigerator status") end
    end)
    return
  end
  if not sp.supported then
    commandFailed(sp.name .. " setpoint is not available on this refrigerator")
    return
  end
  if (sp.min and value < sp.min) or (sp.max and value > sp.max) then
    commandFailed(string.format("%s setpoint %s is outside the allowed range %s..%s %s", sp.name,
      fmtNumber(value), fmtNumber(sp.min), fmtNumber(sp.max), tostring(sp.unit or "")))
    publishSetpoint(sp, true)
    return
  end
  if value == sp.value then return end
  local comp = sp.comp
  sendCommand(comp, "thermostatCoolingSetpoint", "setCoolingSetpoint", { value }, function(ok, code, err)
    if not ok then
      commandFailed(sp.name .. " setpoint: " .. tostring(err))
      publishSetpoint(sp, true)
      return
    end
    startConfirm("setpoint:" .. sp.key, sp.name .. " setpoint " .. fmtTemp(value, sp.unit),
      function() return sp.value == value end,
      function() publishSetpoint(sp, true) end)
  end)
end

function ResetWaterFilter()
  if not preflight(nil) then return end
  sendCommand("main", "custom.waterFilter", "resetWaterFilter", nil, function(ok, code, err)
    if not ok then
      commandFailed("Water filter reset: " .. tostring(err))
      return
    end
    log("Water filter reset sent")
    setTimer("filterRefresh", 5000, function() RefreshStatus() end)
  end)
end

------------------------------------------------------------------------------
-- Discovery
------------------------------------------------------------------------------
local function cleanLabel(s)
  return trim((tostring(s or "Refrigerator"):gsub(",", " ")))
end

local function isFridge(d)
  if dig(d, "ocf", "ocfDeviceType") == "oic.d.refrigerator" then return true end
  if type(d.presentationId) == "string" and d.presentationId:find("^DA%-REF") then return true end
  for _, comp in ipairs(d.components or {}) do
    for _, cat in ipairs(comp.categories or {}) do
      if cat.name == "Refrigerator" then return true end
    end
  end
  return false
end

local function publishDeviceList(items)
  gDevices = {}
  local names = {}
  local currentId = deviceId()
  local selected
  for _, d in ipairs(items) do
    local label = cleanLabel(d.label or d.name) .. " (" .. tostring(d.deviceId):sub(1, 8) .. ")"
    gDevices[label] = d.deviceId
    names[#names + 1] = label
    if d.deviceId == currentId then selected = label end
  end
  if #names == 0 then
    C4:UpdatePropertyList("Select Refrigerator", DISCOVER_PROMPT, DISCOVER_PROMPT)
    return
  end
  if not selected and currentId == "" and #names == 1 then
    selected = names[1]
    setProp("Device ID", items[1].deviceId)
    log("Auto-selected the only refrigerator found: " .. selected)
  end
  if not selected then
    table.insert(names, 1, DISCOVER_PROMPT)
    selected = DISCOVER_PROMPT
  end
  C4:UpdatePropertyList("Select Refrigerator", table.concat(names, ","), selected)
end

local function fetchAllDevices(url, acc, cb)
  apiRequest("GET", url, nil, function(ok, code, data, err)
    if not ok then
      cb(false, err)
      return
    end
    for _, d in ipairs(dig(data, "items") or {}) do acc[#acc + 1] = d end
    local nextUrl = dig(data, "_links", "next", "href")
    if type(nextUrl) == "string" and nextUrl ~= "" and #acc < 2000 then
      fetchAllDevices(nextUrl, acc, cb)
    else
      cb(true, acc)
    end
  end)
end

function DiscoverDevices(silent)
  if not silent then setDriverStatus("Discovering refrigerators...") end
  fetchAllDevices("/devices", {}, function(ok, all)
    if not ok then
      logError("Discovery failed: " .. tostring(all))
      setDriverStatus("Discovery failed: " .. tostring(all))
      return
    end
    local list = {}
    for _, d in ipairs(all) do
      if isFridge(d) then list[#list + 1] = d end
    end
    log("Discovery found " .. #list .. " refrigerator(s) among " .. #all .. " device(s)")
    publishDeviceList(list)
    if silent then return end
    if #list == 0 then
      setDriverStatus("No refrigerators found in this SmartThings account")
    elseif deviceId() == "" then
      setDriverStatus("Select your refrigerator in 'Select Refrigerator'")
    else
      setDriverStatus("Refrigerator selected - reading status")
      RefreshStatus()
    end
  end)
end

------------------------------------------------------------------------------
-- OAuth app creation (uses the Personal Access Token once) and authorization
------------------------------------------------------------------------------
function CreateOAuthApp()
  local pat = trim(Properties["Personal Access Token"])
  local clientId, _, redirect = oauthClient()
  if pat == "" then
    setProp("Authorization Status", "Paste a Personal Access Token first (see Documentation)")
    return
  end
  if clientId ~= "" then
    setProp("Authorization Status", "OAuth Client ID is already set - clear it first to create a new app")
    return
  end
  if redirect == "" then
    setProp("Authorization Status", "Set OAuth Redirect URI first")
    return
  end
  local body = {
    appName = "directorlink-samsung-fridge-" .. randomHex(8) .. "-" .. randomHex(4) .. "-" .. randomHex(12),
    displayName = "DirectorLink Samsung Refrigerator",
    description = "DirectorLink Control4 driver for Samsung refrigerators",
    appType = "API_ONLY",
    classifications = { "CONNECTED_SERVICE" },
    singleInstance = true,
    principalType = "LOCATION",
    apiOnly = {},
    oauth = {
      clientName = "DirectorLink Samsung Refrigerator",
      scope = OAUTH_SCOPES,
      redirectUris = { redirect },
    },
  }
  setProp("Authorization Status", "Creating OAuth app...")
  local headers = {
    ["Authorization"] = "Bearer " .. pat,
    ["Content-Type"] = "application/json",
    ["Accept"] = "application/json",
    ["User-Agent"] = USER_AGENT,
  }
  httpRequest("POST", API_BASE .. "/apps", JSON.encode(body), headers, function(errCode, errMsg, code, respBody)
    if errCode ~= 0 or not code then
      setProp("Authorization Status", "Create app failed: network error " .. tostring(errMsg))
      return
    end
    local decoded = safeDecode(respBody)
    if code >= 200 and code < 300 and dig(decoded, "oauthClientId") then
      C4:UpdateProperty("OAuth Client ID", decoded.oauthClientId)
      C4:UpdateProperty("OAuth Client Secret", decoded.oauthClientSecret or "")
      log("OAuth app created (appId " .. tostring(dig(decoded, "app", "appId")) .. ")")
      updateAuthUrl()
      setProp("Authorization Status",
        "OAuth app created - open the Authorization URL in a browser, then paste the code")
      ShowAuthUrl()
    else
      local msg = describeHttpError(code, decoded)
      if code == 401 or code == 403 then
        msg = msg .. " - the token needs ALL Apps scopes and must not be expired (see Documentation)"
      end
      logError("Create OAuth app failed: " .. msg)
      setProp("Authorization Status", "Create app failed: " .. msg)
    end
  end, true)
end

function ShowAuthUrl()
  local url = buildAuthUrl()
  updateAuthUrl()
  if url == "" then
    log("Set OAuth Client ID and Redirect URI first")
    return
  end
  print("")
  print("==== SmartThings authorization ====")
  print("1) Open this URL in a browser and sign in with the Samsung account that owns the refrigerator:")
  print(url)
  print("2) Choose the location, approve, and copy the address of the page you land on (it contains code=...).")
  print("3) Paste it into the 'Authorization Code' property right away (codes expire within minutes).")
  print("===================================")
end

local function exchangeAuthCode(raw)
  local code = extractAuthCode(raw)
  if not code or code == "" then
    setProp("Authorization Status", "Could not find a code in the pasted text")
    return
  end
  local clientId, _, redirect = oauthClient()
  if redirect == "" then
    setProp("Authorization Status", "Set OAuth Redirect URI first")
    return
  end
  setProp("Authorization Status", "Exchanging authorization code...")
  tokenRequest({
    { "grant_type", "authorization_code" },
    { "code", code },
    { "client_id", clientId },
    { "redirect_uri", redirect },
  }, function(ok, err)
    if ok then
      log("Authorization successful")
      setDriverStatus("Authorized - discovering refrigerators")
      UpdateUI()
      DiscoverDevices()
    else
      logError("Authorization code exchange failed: " .. tostring(err))
      setProp("Authorization Status", "Authorization failed: " .. tostring(err) ..
        " (codes expire within minutes - open the URL again)")
    end
  end)
end

------------------------------------------------------------------------------
-- Polling / housekeeping
------------------------------------------------------------------------------
local function startPolling()
  local minutes = tonumber(Properties["Poll Interval (minutes)"]) or 2
  if minutes < 1 then minutes = 1 end
  setTimer("poll", minutes * 60 * 1000, function()
    if deviceId() ~= "" and bearerToken() and next(gOps) == nil then RefreshStatus() end
  end, true)
end

local function startHousekeeping()
  setTimer("housekeeping", HOUSEKEEPING_MS, function()
    maybeRenewToken()
    updateAuthStatus()
  end, true)
end

local function resetDeviceState()
  for _, f in ipairs(FEATURES) do
    f.state, f.supported, f.reason, f.variantIndex, f.pending, f.flash = nil, nil, nil, nil, nil, false
  end
  for _, sp in ipairs(SETPOINTS) do
    sp.value, sp.supported, sp.lastList = nil, nil, nil
  end
  gOps = {}
  gState.online, gState.doorOpen, gState.doorOpenSince, gState.doorAlerted = nil, nil, nil, false
  gState.filterStatus, gState.statusRead = nil, false
  persistSet(PERSIST_LAST, {}, false)
end

------------------------------------------------------------------------------
-- Control4 entry points
------------------------------------------------------------------------------
ON_PROPERTY_CHANGED = {}

ON_PROPERTY_CHANGED["Debug Mode"] = function(value)
  gDebug = (value == "On")
end

ON_PROPERTY_CHANGED["Personal Access Token"] = function()
  updateAuthStatus()
end

ON_PROPERTY_CHANGED["OAuth Client ID"] = function() updateAuthUrl() end
ON_PROPERTY_CHANGED["OAuth Client Secret"] = function() updateAuthUrl() end
ON_PROPERTY_CHANGED["OAuth Redirect URI"] = function() updateAuthUrl() end

ON_PROPERTY_CHANGED["Authorization Code"] = function(value)
  if trim(value) == "" then return end
  C4:UpdateProperty("Authorization Code", "")
  exchangeAuthCode(value)
end

ON_PROPERTY_CHANGED["Select Refrigerator"] = function(value)
  local id = gDevices[value]
  if id and id ~= deviceId() then
    C4:UpdateProperty("Device ID", id)
    ON_PROPERTY_CHANGED["Device ID"](id)
  end
end

ON_PROPERTY_CHANGED["Device ID"] = function()
  resetDeviceState()
  setProp("Connection", "Unknown")
  UpdateUI()
  if deviceId() ~= "" and bearerToken() then RefreshStatus() end
end

ON_PROPERTY_CHANGED["Poll Interval (minutes)"] = function()
  startPolling()
end

ON_PROPERTY_CHANGED["Fridge Setpoint"] = function(value)
  local n = tonumber(tostring(value):match("^%s*(-?%d+%.?%d*)"))
  if n then SetSetpoint("fridge", n) end
end

ON_PROPERTY_CHANGED["Freezer Setpoint"] = function(value)
  local n = tonumber(tostring(value):match("^%s*(-?%d+%.?%d*)"))
  if n then SetSetpoint("freezer", n) end
end

function OnPropertyChanged(strProperty)
  local value = Properties[strProperty]
  dbg("OnPropertyChanged:", strProperty)
  local fn = ON_PROPERTY_CHANGED[strProperty]
  if fn then
    local ok, err = pcall(fn, value)
    if not ok then logError("OnPropertyChanged(" .. strProperty .. "): " .. tostring(err)) end
  end
end

ACTIONS = {
  CreateOAuthApp = function() CreateOAuthApp() end,
  ShowAuthUrl = function() ShowAuthUrl() end,
  DiscoverDevices = function() DiscoverDevices() end,
  RefreshStatus = function() RefreshStatus() end,
  ResetWaterFilter = function() ResetWaterFilter() end,
  RenewToken = function() RenewToken() end,
  SignOut = function()
    clearTokens(nil)
    setDriverStatus("Signed out")
  end,
}

COMMANDS = {
  SET_FEATURE = function(p)
    local f = FEATURE_BY_NAME[p.Feature or ""]
    if not f then
      logError("SET_FEATURE: unknown feature " .. tostring(p.Feature))
      return
    end
    if p.State == "Toggle" then
      ToggleFeature(f.key)
    else
      SetFeature(f.key, p.State == "On")
    end
  end,
  SET_FRIDGE_TEMPERATURE = function(p) SetSetpoint("fridge", p.Temperature) end,
  SET_FREEZER_TEMPERATURE = function(p) SetSetpoint("freezer", p.Temperature) end,
  RESET_WATER_FILTER = function() ResetWaterFilter() end,
  REFRESH = function() RefreshStatus() end,
}

function ExecuteCommand(strCommand, tParams)
  tParams = tParams or {}
  dbg("ExecuteCommand:", strCommand, tParams.ACTION)
  local fn
  if strCommand == "LUA_ACTION" then
    fn = ACTIONS[tParams.ACTION or ""]
  else
    fn = COMMANDS[strCommand]
  end
  if fn then
    local ok, err = pcall(fn, tParams)
    if not ok then logError("ExecuteCommand(" .. tostring(strCommand) .. "): " .. tostring(err)) end
  else
    dbg("Unhandled command:", strCommand)
  end
end

local function refreshFromTile()
  if not bearerToken() or deviceId() == "" then
    UpdateStatusTile()
    return
  end
  gState.refreshing = true
  UpdateStatusTile()
  RefreshStatus(function()
    gState.refreshing = false
    UpdateUI()
  end)
end

function ReceivedFromProxy(idBinding, strCommand, tParams)
  dbg("ReceivedFromProxy:", idBinding, strCommand)
  local ok, err = pcall(function()
    if idBinding == STATUS_BINDING and strCommand == "SELECT" then
      refreshFromTile()
      return
    end
    local f = FEATURE_BY_BINDING[idBinding]
    if not f then return end
    if (idBinding == f.binding and strCommand == "SELECT") or (idBinding == f.button and strCommand == "DO_CLICK") then
      ToggleFeature(f.key)
    end
  end)
  if not ok then logError("ReceivedFromProxy: " .. tostring(err)) end
end

function OnBindingChanged(idBinding, strClass, bIsBound)
  local f = FEATURE_BY_BINDING[idBinding]
  if f and idBinding == f.button and bIsBound then
    f.lastLed = nil
    pcall(C4.SendToProxy, C4, f.button, "BUTTON_COLORS",
      { ON_COLOR = { COLOR_STR = "00A0FF" }, OFF_COLOR = { COLOR_STR = "000000" } }, "NOTIFY")
    UpdateFeatureUI(f)
  end
end

function TestCondition(strName, tParams)
  local logic = tParams and tParams.LOGIC or "EQUAL"
  local value = tParams and tParams.VALUE or ""
  local current
  if strName == "CONNECTION" then
    current = (gState.online == false) and "Offline" or "Online"
  elseif strName == "DOOR" then
    current = gState.doorOpen and "Open" or "Closed"
  elseif strName == "WATER_FILTER" then
    current = gState.filterStatus or "OK"
  else
    for _, f in ipairs(FEATURES) do
      if f.cond == strName then current = (f.state == true) and "On" or "Off" end
    end
  end
  if current == nil then return false end
  if logic == "NOT_EQUAL" then return current ~= value end
  return current == value
end

function OnDriverInit(strDIT)
  -- Variables must be added here, always in the same order.
  C4:AddVariable("POWER_COOL", "0", "BOOL", true, false)
  C4:AddVariable("POWER_FREEZE", "0", "BOOL", true, false)
  C4:AddVariable("SABBATH_MODE", "0", "BOOL", true, false)
  C4:AddVariable("ICE_MAKER", "0", "BOOL", true, false)
  C4:AddVariable("ONLINE", "0", "BOOL", true, false)
  C4:AddVariable("DOOR_OPEN", "0", "BOOL", true, false)
  C4:AddVariable("FRIDGE_TEMP", "0", "NUMBER", true, false)
  C4:AddVariable("FREEZER_TEMP", "0", "NUMBER", true, false)
  C4:AddVariable("FRIDGE_SETPOINT", "0", "NUMBER", true, false)
  C4:AddVariable("FREEZER_SETPOINT", "0", "NUMBER", true, false)
  C4:AddVariable("POWER_W", "0", "NUMBER", true, false)
  C4:AddVariable("WATER_FILTER_USAGE", "0", "NUMBER", true, false)
end

function OnDriverLateInit(strDIT)
  math.randomseed(os.time() + (tonumber(C4:GetDeviceID()) or 0))
  setProp("Driver Version", DRIVER_VERSION ~= "dev" and DRIVER_VERSION or ("dev build " .. tostring(C4:GetDriverConfigInfo("version") or "")))
  OnPropertyChanged("Debug Mode")

  local saved = persistGet(PERSIST_AUTH, true)
  if type(saved) == "table" and saved.access_token then gAuth = saved end
  local last = persistGet(PERSIST_LAST, false)
  if type(last) == "table" then
    for _, f in ipairs(FEATURES) do
      if type(last[f.key]) == "boolean" then f.state = last[f.key] end
    end
  end

  updateAuthUrl()
  updateAuthStatus()
  UpdateUI()
  startPolling()
  startHousekeeping()

  if not bearerToken() then
    setDriverStatus("Not configured - see the Documentation tab")
    return
  end
  -- Give the network a moment after a controller restart.
  setTimer("startup", 5000, function()
    maybeRenewToken(function()
      if deviceId() ~= "" then
        RefreshStatus()
        DiscoverDevices(true)  -- repopulate the selection list
      else
        DiscoverDevices()
      end
    end)
  end)
end

function OnDriverDestroyed(strDIT)
  for name in pairs(gTimers) do cancelTimer(name) end
end

log("Driver loaded")
