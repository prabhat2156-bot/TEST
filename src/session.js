const sessions = new Map();

function defaultGroupFlow() {
  return {
    step: "name",
    name: "", count: 1, numbering: true, description: "",
    photo: null, disappearing: 0, members: [], makeAdmin: false,
    permissions: { sendMessages: true, editInfo: true, addMembers: true, approveMembers: false },
  };
}

function defaultFeatureFlow(feature) {
  return {
    feature,            // get_links | leave | remove_members | make_admin | approval | member_list | pending_list
    step: "group_type", // group_type | similar_query | paginate | admin_numbers | executing
    allGroups: [],
    selectedIds: [],
    page: 0,
    keyword: "",
    adminNumbers: [],
  };
}

function defaultSession() {
  return {
    awaitingPhoneForIndex: null,
    groupFlow:    null,
    joinFlow:     null,
    featureFlow:  null,
    lastMsgId:    null,
    cancelMsgId:  null,
    cancelPending: false,
  };
}

function getSession(userId) {
  if (!sessions.has(userId)) sessions.set(userId, defaultSession());
  return sessions.get(userId);
}
function updateSession(userId, patch) {
  sessions.set(userId, { ...getSession(userId), ...patch });
}
function resetSession(userId) {
  sessions.set(userId, defaultSession());
}

module.exports = { getSession, updateSession, resetSession, defaultGroupFlow, defaultFeatureFlow };
