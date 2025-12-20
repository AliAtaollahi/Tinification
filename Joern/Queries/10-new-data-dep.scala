import io.shiftleft.semanticcpg.language._
import io.shiftleft.codepropertygraph.generated.nodes.Method

import scala.collection.mutable
import scala.util.matching.Regex
import scala.util.Try
import java.util.regex.Pattern

/**
 * Usage in Joern:
 *   :load /path/to/this.scala
 *   val dot = CfgByClass.run(".*AB_StaticEquivalent\\.java")
 */
object CfgByClass {

  // ---------------- helpers ----------------

  private def asString(x: Any): String = x match {
    case null                => ""
    case s: String           => s
    case it: Iterator[_]     => it.collect { case s: String => s }.toSeq.headOption.getOrElse("")
    case xs: IterableOnce[_] => xs.iterator.collect { case s: String => s }.toSeq.headOption.getOrElse("")
    case other               => other.toString
  }

  private def toIntOpt(s: String): Option[Int] = Try(s.toInt).toOption

  private def ownerTypeOf(fullName: String): String =
    fullName.takeWhile(_ != '.')

  private def simpleTypeName(tf: String): String =
    Option(tf).getOrElse("").split("[.$]").lastOption.getOrElse(tf)

  private def html(s: String): String =
    s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

  private def sanitizeId(s: String): String =
    s.replaceAll("[^A-Za-z0-9_]", "_")

  private def prefixIds(dot: String, prefix: String): String = {
    val idRx = "\"(\\d+)\"".r
    idRx.replaceAllIn(dot, m => "\"" + prefix + "_" + m.group(1) + "\"")
  }

  private def stripDotWrapper(dot: String): String =
    dot.linesIterator
      .filterNot(_.trim.startsWith("digraph"))
      .filterNot(_.trim.startsWith("node "))
      .filterNot(_.trim == "}")
      .mkString("\n")

  // ---------------- method label / ctor ----------------

  private def prettyMethodLabel(m: Method): String = {
    val mFull  = asString(m.fullName)
    val owner0 = asString(m.typeDecl.name)
    val owner  = if (owner0.nonEmpty) owner0 else ownerTypeOf(mFull)

    val params =
      m.parameter.orderGt(0).l.map { p =>
        val nm  = Option(p.name).map(_.trim).filter(n => n.nonEmpty && n != "this")
        val tpe = simpleTypeName(Option(p.typeFullName).getOrElse(""))
        nm.getOrElse(tpe)
      }.mkString(", ")

    s"$owner.${asString(m.name)}($params)"
  }

  private def isCtor(m: Method): Boolean = {
    val n = asString(m.name)
    val f = asString(m.fullName)
    (n == "<init>" || n == "init") || f.contains("<init>")
  }

  // ---------------- def/use extraction ----------------

  private val assignOpRx: Regex =
    "(\\+=|-=|\\*=|/=|%=|<<=|>>=|\\|=|&=|\\^=|(?<![!<>=])=(?!=))".r

  private def wordRx(name: String): Regex =
    ("(?<![\\w$])" + Pattern.quote(name) + "(?![\\w$])").r

  private def maybeThisFieldRx(field: String): Regex =
    ("(?<![\\w$])(?:this\\.)?" + Pattern.quote(field) + "(?![\\w$])").r

  private def extractDefsUses(
      code0: String,
      ownerFields: Set[String],
      paramNames: Set[String]
  ): (Set[String], Set[String]) = {

    val code = Option(code0).getOrElse("").trim
    val defs = mutable.Set.empty[String]
    val uses = mutable.Set.empty[String]

    def addFieldDef(f: String): Unit = defs += s"SV::$f"
    def addFieldUse(f: String): Unit = uses += s"SV::$f"
    def addParamDef(p: String): Unit = defs += s"P::$p"
    def addParamUse(p: String): Unit = uses += s"P::$p"

    // ++ / -- are read-modify-write
    ownerFields.foreach { f =>
      val base = maybeThisFieldRx(f).regex
      val rx1  = (base + "\\s*(\\+\\+|--)").r
      val rx2  = ("(\\+\\+|--)\\s*" + base).r
      if (rx1.findFirstIn(code).nonEmpty || rx2.findFirstIn(code).nonEmpty) {
        addFieldDef(f); addFieldUse(f)
      }
    }
    paramNames.foreach { p =>
      val base = wordRx(p).regex
      val rx1  = (base + "\\s*(\\+\\+|--)").r
      val rx2  = ("(\\+\\+|--)\\s*" + base).r
      if (rx1.findFirstIn(code).nonEmpty || rx2.findFirstIn(code).nonEmpty) {
        addParamDef(p); addParamUse(p)
      }
    }

    assignOpRx.findFirstMatchIn(code) match {
      case Some(m) =>
        val op  = m.group(1)
        val lhs = code.substring(0, m.start)
        val rhs = code.substring(m.end)

        ownerFields.foreach { f => if (maybeThisFieldRx(f).findFirstIn(lhs).nonEmpty) addFieldDef(f) }
        paramNames.foreach { p => if (wordRx(p).findFirstIn(lhs).nonEmpty) addParamDef(p) }

        ownerFields.foreach { f => if (maybeThisFieldRx(f).findFirstIn(rhs).nonEmpty) addFieldUse(f) }
        paramNames.foreach { p => if (wordRx(p).findFirstIn(rhs).nonEmpty) addParamUse(p) }

        if (op != "=") {
          ownerFields.foreach { f => if (maybeThisFieldRx(f).findFirstIn(lhs).nonEmpty) addFieldUse(f) }
          paramNames.foreach { p => if (wordRx(p).findFirstIn(lhs).nonEmpty) addParamUse(p) }
        }

      case None =>
        ownerFields.foreach { f => if (maybeThisFieldRx(f).findFirstIn(code).nonEmpty) addFieldUse(f) }
        paramNames.foreach { p => if (wordRx(p).findFirstIn(code).nonEmpty) addParamUse(p) }
    }

    (defs.toSet, uses.toSet)
  }

  // ---------------- DOT abstraction (prefer CALL nodes per line) ----------------

  case class AbsNode(id: String, kind: String, line: String, code: String)
  case class AbstractedCfg(dot: String, nodes: List[AbsNode], cfgEdges: Set[(String, String)])

  private def isMethodKind(k: String): Boolean =
    k == "METHOD" || k.startsWith("METHOD,")

  private def isOperatorKind(k: String): Boolean =
    Option(k).exists(_.startsWith("<operator>"))

  private def isReturnLike(k: String): Boolean =
    k == "METHOD_RETURN" || k == "RETURN" || k.endsWith("RETURN")

  private def looksLikeCall(code: String, kind: String): Boolean = {
    val c = Option(code).getOrElse("")
    val hasParens = c.contains("(") && c.contains(")")
    hasParens && !isOperatorKind(kind) && !isMethodKind(kind) && !isReturnLike(kind)
  }

  private def chooseBestNodeForLine(nodes: List[AbsNode]): AbsNode = {
    def score(n: AbsNode): (Int, Int) = {
      val s1 =
        if (isMethodKind(n.kind)) 1000
        else if (looksLikeCall(n.code, n.kind)) 900
        else if (isOperatorKind(n.kind)) 700
        else 500
      (s1, n.code.length)
    }
    nodes.maxBy(score)
  }

  private def abstractDotByLine(dot: String, rootLabel: String): AbstractedCfg = {
    val nodeRx: Regex =
      "\"(\\d+)\"\\s*\\[label\\s*=\\s*<([^,>]+),\\s*(\\d+)<BR/>(.*?)>\\s*\\]".r
    val edgeRx: Regex =
      "\"(\\d+)\"\\s*->\\s*\"(\\d+)\"".r

    val nodesAll: List[AbsNode] =
      nodeRx.findAllMatchIn(dot).map { m =>
        AbsNode(m.group(1), m.group(2), m.group(3), m.group(4))
      }.toList

    val edgesAll: List[(String, String)] =
      edgeRx.findAllMatchIn(dot).map(m => (m.group(1), m.group(2))).toList

    if (nodesAll.isEmpty && edgesAll.isEmpty) {
      val empty = """digraph "cfg_abstract" { node [shape="rect"]; }"""
      return AbstractedCfg(empty, Nil, Set.empty)
    }

    val bestByLine: Map[String, AbsNode] =
      nodesAll.groupBy(_.line).view.mapValues(chooseBestNodeForLine).toMap

    val nodeById: Map[String, AbsNode] = nodesAll.iterator.map(n => n.id -> n).toMap
    val keptNodes: List[AbsNode] = bestByLine.values.toList

    val keptCfgEdges: Set[(String, String)] =
      edgesAll.flatMap { case (srcId, dstId) =>
        val adjSrc = nodeById.get(srcId).flatMap(n => bestByLine.get(n.line)).map(_.id).getOrElse(srcId)
        val adjDst = nodeById.get(dstId).flatMap(n => bestByLine.get(n.line)).map(_.id).getOrElse(dstId)
        if (adjSrc != adjDst) Some(adjSrc -> adjDst) else None
      }.toSet

    val nodeLines: List[String] =
      keptNodes
        .sortBy(n => toIntOpt(n.line).getOrElse(Int.MaxValue))
        .map { n =>
          if (isMethodKind(n.kind)) {
            s""""${n.id}" [label = <<FONT>${html(rootLabel)}</FONT>> ]"""
          } else {
            s""""${n.id}" [label = <${n.kind}, ${n.line}<BR/>${n.code}> ]"""
          }
        }

    val edgeLines: List[String] =
      keptCfgEdges.toList.map { case (s, t) => s""""$s" -> "$t" [label="cfg"]""" }

    val outDot =
      s"""digraph "cfg_abstract" {
node [shape="rect"];
${(nodeLines ++ edgeLines).mkString("\n")}
}"""

    AbstractedCfg(outDot, keptNodes, keptCfgEdges)
  }

  // ---------------- per-method info ----------------

  case class MethodInfo(
    methodKey: String,
    methodFull: String,
    owner: String,
    typeKey: String,
    pfx: String,
    pretty: String,
    bodyDotStripped: String,

    stmtIds: Set[String],
    entryNodeId: String,

    lineByNode: Map[String, Int],
    codeByNode: Map[String, String],
    kindByNode: Map[String, String],

    preds: Map[String, Set[String]],
    defVars: Map[String, Set[String]],
    useVars: Map[String, Set[String]],

    fieldsTracked: Set[String],
    paramsTracked: Set[String],
    isConstructor: Boolean
  )

  // ---------------- action dependency helpers ----------------

  private def isMessageSendCall(callName: String, callCode: String): Boolean = {
    val n = Option(callName).getOrElse("").trim
    val c = Option(callCode).getOrElse("").trim
    if (n.isEmpty) return false
    if (n.startsWith("<operator>")) return false
    if (n == "<init>" || n == "init") return false
    if (!(c.contains("(") && c.contains(")"))) return false
    if (c.startsWith("super(") || c.startsWith("this(")) return false
    if (c.startsWith("new ") || c.contains(" new ")) return false
    true
  }

  private def pickCallNodeId(mi: MethodInfo, ln: Option[Int], callName: String, callCode: String): Option[String] = {
    val byLine: List[String] =
      ln.toList.flatMap { l =>
        mi.lineByNode.collect { case (nid, ll) if ll == l => nid }.toList
      }

    def best(cands: List[String]): Option[String] = {
      if (cands.isEmpty) None
      else {
        val scored = cands.map { nid =>
          val k = mi.kindByNode.getOrElse(nid, "")
          val c = mi.codeByNode.getOrElse(nid, "")
          val looks = looksLikeCall(c, k)
          val nameHit = callName.nonEmpty && (c.contains(callName + "(") || k == callName)
          val codeHit = callCode.nonEmpty && (c == callCode || c.contains(callCode) || callCode.contains(c))
          val score =
            (if (looks) 100 else 0) +
              (if (nameHit) 50 else 0) +
              (if (codeHit) 30 else 0) +
              c.length
          (nid, score)
        }
        Some(scored.maxBy(_._2)._1)
      }
    }

    best(byLine).orElse {
      val cands =
        mi.codeByNode.collect {
          case (nid, c) if
            (callName.nonEmpty && (c.contains(callName + "(") || mi.kindByNode.getOrElse(nid, "") == callName)) ||
              (callCode.nonEmpty && (c == callCode || c.contains(callCode) || callCode.contains(c)))
          => nid
        }.toList
      best(cands)
    }
  }

  // ---------------- main ----------------

  def run(fileRegex: String = ".*\\.java"): String = {

    val methods: List[Method] =
      cpg.method
        .where(_.isExternal(false))
        .where(_.file.name(fileRegex))
        .l

    val methodInfos = mutable.ListBuffer.empty[MethodInfo]

    methods.foreach { m =>
      val raw = m.dotCfg.l.headOption.getOrElse("")
      if (raw.nonEmpty) {

        val mid     = m.id.toString
        val mFull   = asString(m.fullName)
        val pretty  = prettyMethodLabel(m)

        val owner0  = asString(m.typeDecl.name)
        val owner   = if (owner0.nonEmpty) owner0 else ownerTypeOf(mFull)

        val typeKey0 = asString(m.typeDecl.fullName)
        val typeKey  = if (typeKey0.nonEmpty) typeKey0 else owner

        val pfx = sanitizeId(owner + "__" + mid + "__" + asString(m.name))

        val absCfg = abstractDotByLine(raw, pretty)
        val body   = stripDotWrapper(prefixIds(absCfg.dot, pfx))

        def pref(id: String): String = pfx + "_" + id

        val prefNodes = absCfg.nodes.map(n => n.copy(id = pref(n.id)))
        val stmtIds: Set[String] = prefNodes.map(_.id).toSet

        val lineByNode: Map[String, Int] =
          prefNodes.map(n => n.id -> toIntOpt(n.line).getOrElse(Int.MaxValue)).toMap
        val codeByNode: Map[String, String] =
          prefNodes.map(n => n.id -> n.code).toMap
        val kindByNode: Map[String, String] =
          prefNodes.map(n => n.id -> n.kind).toMap

        val entryNodeId: String =
          prefNodes.find(n => isMethodKind(n.kind)).map(_.id)
            .getOrElse(stmtIds.minBy(id => lineByNode.getOrElse(id, Int.MaxValue)))

        val predsM = mutable.Map.empty[String, Set[String]].withDefaultValue(Set.empty)
        absCfg.cfgEdges.foreach { case (s0, t0) =>
          val s = pref(s0); val t = pref(t0)
          if (stmtIds.contains(s) && stmtIds.contains(t)) predsM(t) = predsM(t) + s
        }
        val preds: Map[String, Set[String]] = predsM.toMap.withDefaultValue(Set.empty)

        val declaredFields: Set[String] = m.typeDecl.member.name.l.toSet
        val methodText: String          = absCfg.nodes.map(_.code).mkString("\n")
        val fieldsTracked: Set[String]  =
          declaredFields.filter(f => maybeThisFieldRx(f).findFirstIn(methodText).nonEmpty)

        val paramsTracked: Set[String] =
          m.parameter.orderGt(0).name.l.map(_.trim).filter(_.nonEmpty).toSet

        val defVarsM = mutable.Map.empty[String, Set[String]].withDefaultValue(Set.empty)
        val useVarsM = mutable.Map.empty[String, Set[String]].withDefaultValue(Set.empty)

        prefNodes.foreach { n =>
          val (defs, uses) = extractDefsUses(n.code, fieldsTracked, paramsTracked)
          defVarsM(n.id) = defs
          useVarsM(n.id) = uses
        }

        methodInfos += MethodInfo(
          methodKey       = mid,
          methodFull      = mFull,
          owner           = owner,
          typeKey         = typeKey,
          pfx             = pfx,
          pretty          = pretty,
          bodyDotStripped = body,

          stmtIds         = stmtIds,
          entryNodeId     = entryNodeId,

          lineByNode      = lineByNode,
          codeByNode      = codeByNode,
          kindByNode      = kindByNode,

          preds           = preds,
          defVars         = defVarsM.toMap.withDefaultValue(Set.empty),
          useVars         = useVarsM.toMap.withDefaultValue(Set.empty),

          fieldsTracked   = fieldsTracked,
          paramsTracked   = paramsTracked,
          isConstructor   = isCtor(m)
        )
      }
    }

    val infoByFull: Map[String, MethodInfo] =
      methodInfos.toList.map(mi => mi.methodFull -> mi).toMap

    // ---------------- ctor seeds for data dep ----------------

    val initSeedsByType: Map[String, Map[String, String]] = {
      methodInfos.toList.groupBy(_.typeKey).map { case (typeKey, infos) =>
        val lastDefByVar = mutable.Map.empty[String, (Int, String)]
        infos.filter(_.isConstructor).foreach { initInfo =>
          initInfo.stmtIds.foreach { nid =>
            val ln = initInfo.lineByNode.getOrElse(nid, Int.MaxValue)
            initInfo.defVars.getOrElse(nid, Set.empty).foreach { v =>
              if (v.startsWith("SV::")) {
                lastDefByVar.get(v) match {
                  case Some((oldLn, _)) if oldLn >= ln =>
                  case _ => lastDefByVar.update(v, (ln, nid))
                }
              }
            }
          }
        }
        typeKey -> lastDefByVar.toMap.view.mapValues(_._2).toMap
      }
    }

    val nodeToMethodKey: Map[String, String] =
      methodInfos.toList.flatMap(mi => mi.stmtIds.map(id => id -> mi.methodKey)).toMap

    // ---------------- DATA DEP edges ----------------

    type DefPair = (String, String)
    val dataDepEdges = mutable.Set.empty[String]

    methodInfos.foreach { mi =>
      val stmtIds = mi.stmtIds
      if (stmtIds.nonEmpty) {

        val sources = stmtIds.filter(id => mi.preds.getOrElse(id, Set.empty).isEmpty)
        val startNodes =
          if (sources.nonEmpty) sources
          else Set(stmtIds.minBy(id => mi.lineByNode.getOrElse(id, Int.MaxValue)))

        val initSeedMap = initSeedsByType.getOrElse(mi.typeKey, Map.empty)

        val svSeeds: Set[DefPair] =
          if (mi.isConstructor) Set.empty
          else mi.fieldsTracked.flatMap { f =>
            val v = s"SV::$f"
            initSeedMap.get(v).map(defNodeId => (v, defNodeId))
          }

        val paramSeedsAtStart: Set[DefPair] =
          startNodes.flatMap(start => mi.paramsTracked.map(p => (s"P::$p", start)))

        val initialDefs: Set[DefPair] = paramSeedsAtStart ++ svSeeds

        def gen(nodeId: String): Set[DefPair] =
          mi.defVars.getOrElse(nodeId, Set.empty).map(v => (v, nodeId))

        var inMap: Map[String, Set[DefPair]] =
          stmtIds.map(id => id -> Set.empty[DefPair]).toMap
        var outMap: Map[String, Set[DefPair]] =
          stmtIds.map(id => id -> Set.empty[DefPair]).toMap

        // init
        stmtIds.foreach { id =>
          val baseIn = if (startNodes.contains(id)) initialDefs else Set.empty[DefPair]
          val predIn = mi.preds.getOrElse(id, Set.empty).flatMap(p => outMap.getOrElse(p, Set.empty)).toSet
          val in0    = baseIn ++ predIn
          inMap      = inMap.updated(id, in0)

          val kill   = mi.defVars.getOrElse(id, Set.empty)
          val out0   = gen(id) ++ in0.filterNot { case (v, _) => kill.contains(v) }
          outMap     = outMap.updated(id, out0)
        }

        // fixpoint
        var changed = true
        while (changed) {
          changed = false
          for (id <- stmtIds) {
            val baseIn = if (startNodes.contains(id)) initialDefs else Set.empty[DefPair]
            val predIn = mi.preds.getOrElse(id, Set.empty).flatMap(p => outMap.getOrElse(p, Set.empty)).toSet
            val newIn  = baseIn ++ predIn

            val kill   = mi.defVars.getOrElse(id, Set.empty)
            val newOut = gen(id) ++ newIn.filterNot { case (v, _) => kill.contains(v) }

            if (newIn != inMap.getOrElse(id, Set.empty) || newOut != outMap.getOrElse(id, Set.empty)) {
              inMap  = inMap.updated(id, newIn)
              outMap = outMap.updated(id, newOut)
              changed = true
            }
          }
        }

        for (useNodeId <- stmtIds; v <- mi.useVars.getOrElse(useNodeId, Set.empty)) {
          val reachingDefs: Set[String] =
            inMap.getOrElse(useNodeId, Set.empty).collect { case (`v`, defId) => defId }

          reachingDefs.foreach { defId =>
            if (defId != useNodeId) {
              val defMk = nodeToMethodKey.getOrElse(defId, "")
              val useMk = nodeToMethodKey.getOrElse(useNodeId, "")

              val ok =
                if (defMk.nonEmpty && defMk == useMk) {
                  val dl = mi.lineByNode.getOrElse(defId, Int.MaxValue)
                  val ul = mi.lineByNode.getOrElse(useNodeId, Int.MaxValue)
                  dl < ul
                } else true

              if (ok) dataDepEdges += s""""$defId" -> "$useNodeId" [label="data dep", color="purple"]"""
            }
          }
        }
      }
    }

    // ---------------- ACTION DEP edges (UPDATED: add new-><init> edges) ----------------
    //
    // Rule:
    // - caller must NOT be a constructor
    // - for non-ctor callees: only when the call looks like a message-send
    // - for ctor callees (<init>): ALWAYS add (this gives main's `new A()` -> `A.<init>`)

    val actDepEdges = mutable.Set.empty[String]

    val nonCtorCallers: List[Method] = methods.filterNot(isCtor)

    nonCtorCallers.foreach { callerM =>
      val callerFull = asString(callerM.fullName)
      infoByFull.get(callerFull).foreach { callerInfo =>

        callerM.call.l.foreach { call =>
          val callName = asString(call.name)
          val callCode = asString(call.code)
          val callLn   = call.lineNumber.map(_.toInt)

          val internalCallees: List[MethodInfo] =
            call.callee.l
              .map(cm => asString(cm.fullName))
              .distinct
              .flatMap(fn => infoByFull.get(fn))

          if (internalCallees.nonEmpty) {
            val callNodeIdOpt = pickCallNodeId(callerInfo, callLn, callName, callCode)
            callNodeIdOpt.foreach { callNodeId =>

              internalCallees.foreach { calleeInfo =>
                if (calleeInfo.isConstructor) {
                  // constructor call (new X()) -> X.<init>
                  actDepEdges += s""""$callNodeId" -> "${calleeInfo.entryNodeId}" [label="act dep", color="red"]"""
                } else {
                  // normal msg-server/procedure call
                  if (isMessageSendCall(callName, callCode)) {
                    actDepEdges += s""""$callNodeId" -> "${calleeInfo.entryNodeId}" [label="act dep", color="red"]"""
                  }
                }
              }
            }
          }
        }
      }
    }

    // --- clusters: class cluster containing per-method subclusters ---
    val clustersByClass: List[String] =
      methodInfos.toList.groupBy(_.owner).toList.sortBy(_._1).flatMap { case (owner, infos) =>
        val ownerSan = sanitizeId(owner)
        val methodClusters =
          infos.sortBy(_.pretty).map { mi =>
            s"""subgraph "cluster_${ownerSan}_m${mi.methodKey}" {
  label="${html(mi.pretty)}";
  ${mi.bodyDotStripped}
}"""
          }
        if (methodClusters.isEmpty) None
        else Some(
          s"""subgraph "cluster_$ownerSan" {
  label="${html(owner)}";
  ${methodClusters.mkString("\n")}
}"""
        )
      }

    val out =
      s"""digraph "program_cfg_abstract_by_class" {
compound=true;
node [shape="rect"];
${clustersByClass.mkString("\n\n")}

${(dataDepEdges.toList.sorted ++ actDepEdges.toList.sorted).mkString("\n")}
}"""

    println(out)
    out
  }
}

// Run
val dot = CfgByClass.run(".*AB_StaticEquivalent\\.java")
