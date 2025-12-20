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

  // ---------------- robust property helpers (avoid Char|String issues) ----------------

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

  private val thisFieldRx: Regex = "\\bthis\\.([A-Za-z_]\\w*)".r

  // assignment operators; plain "=" must not be "==" etc.
  private val assignOpRx: Regex =
    "(\\+=|-=|\\*=|/=|%=|<<=|>>=|\\|=|&=|\\^=|(?<![!<>=])=(?!=))".r

  private def wordRx(name: String): Regex =
    ("(?<![\\w$])" + Pattern.quote(name) + "(?![\\w$])").r

  private def maybeThisFieldRx(field: String): Regex =
    ("(?<![\\w$])(?:this\\.)?" + Pattern.quote(field) + "(?![\\w$])").r

  /**
    * Extract defs/uses from one statement string.
    * Track only:
    *   SV::<field> for this.field (also matches field without explicit this.)
    *   P::<param>  for parameters
    */
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

        // defs: vars in LHS
        ownerFields.foreach { f => if (maybeThisFieldRx(f).findFirstIn(lhs).nonEmpty) addFieldDef(f) }
        paramNames.foreach { p => if (wordRx(p).findFirstIn(lhs).nonEmpty) addParamDef(p) }

        // uses: vars in RHS
        ownerFields.foreach { f => if (maybeThisFieldRx(f).findFirstIn(rhs).nonEmpty) addFieldUse(f) }
        paramNames.foreach { p => if (wordRx(p).findFirstIn(rhs).nonEmpty) addParamUse(p) }

        // compound assigns read old LHS value too
        if (op != "=") {
          ownerFields.foreach { f => if (maybeThisFieldRx(f).findFirstIn(lhs).nonEmpty) addFieldUse(f) }
          paramNames.foreach { p => if (wordRx(p).findFirstIn(lhs).nonEmpty) addParamUse(p) }
        }

      case None =>
        // no assignment: everything is use
        ownerFields.foreach { f => if (maybeThisFieldRx(f).findFirstIn(code).nonEmpty) addFieldUse(f) }
        paramNames.foreach { p => if (wordRx(p).findFirstIn(code).nonEmpty) addParamUse(p) }
    }

    (defs.toSet, uses.toSet)
  }

  // ---------------- DOT abstraction ----------------

  case class AbsNode(id: String, kind: String, line: String, code: String, methodName: Option[String] = None)

  case class AbstractedCfg(
    dot: String,
    nodes: List[AbsNode],
    cfgEdges: Set[(String, String)]
  )

  /**
    * Collapse DOT nodes by source line (keep longest code), rewire CFG edges.
    * CFG edges are labeled "cfg".
    */
  private def abstractDotByLine(dot: String, rootLabelOverride: Option[String]): AbstractedCfg = {
    val nodeRx: Regex =
      "\"(\\d+)\"\\s*\\[label\\s*=\\s*<([^,>]+),\\s*(\\d+)<BR/>(.*?)>\\s*\\]".r

    val edgeRx: Regex =
      "\"(\\d+)\"\\s*->\\s*\"(\\d+)\"".r

    val nodesAll: List[AbsNode] =
      nodeRx.findAllMatchIn(dot).map { m =>
        val kind = m.group(2)
        val code = m.group(4)
        val methName =
          if (kind.startsWith("METHOD") || kind.equalsIgnoreCase("init")) Some(code) else None
        AbsNode(m.group(1), kind, m.group(3), code, methName)
      }.toList

    val edgesAll: List[(String, String)] =
      edgeRx.findAllMatchIn(dot).map(m => (m.group(1), m.group(2))).toList

    if (nodesAll.isEmpty && edgesAll.isEmpty) {
      val empty = """digraph "cfg_abstract" { node [shape="rect"]; }"""
      return AbstractedCfg(empty, Nil, Set.empty)
    }

    val bestByLine: Map[String, AbsNode] =
      nodesAll.groupBy(_.line).map { case (line, xs) => line -> xs.maxBy(_.code.length) }

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
          val label = s"${n.kind}, ${n.line}<BR/>${n.code}"
          s""""${n.id}" [label = <${label}> ]"""
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
    methodFull: String,
    owner: String,
    typeKey: String,
    pfx: String,
    bodyDotStripped: String,
    stmtIds: Set[String],
    lineByNode: Map[String, Int],
    preds: Map[String, Set[String]],
    defVars: Map[String, Set[String]],
    useVars: Map[String, Set[String]],
    fieldsTracked: Set[String],
    paramsTracked: Set[String],
    isConstructor: Boolean
  )

  // ---------------- main entry ----------------

  def run(fileRegex: String = ".*\\.java"): String = {

    val methods =
      cpg.method
        .where(_.isExternal(false))
        .where(_.file.name(fileRegex))
        .l

    val methodInfos = mutable.ListBuffer.empty[MethodInfo]

    // Build per-method info (CFG + def/use)
    methods.foreach { m =>
      val raw = m.dotCfg.l.headOption.getOrElse("")
      if (raw.nonEmpty) {

        val mFull   = asString(m.fullName)
        val owner0  = asString(m.typeDecl.name)
        val owner   = if (owner0.nonEmpty) owner0 else ownerTypeOf(mFull)

        val typeKey0 = asString(m.typeDecl.fullName)
        val typeKey  = if (typeKey0.nonEmpty) typeKey0 else owner

        val pfx = (owner + "__" + mFull).replaceAll("[^A-Za-z0-9_]", "_")

        val absCfg = abstractDotByLine(raw, Some(prettyMethodLabel(m)))
        val body   = stripDotWrapper(prefixIds(absCfg.dot, pfx))

        def pref(id: String): String = pfx + "_" + id

        val prefNodes = absCfg.nodes.map(n => n.copy(id = pref(n.id)))
        val stmtIds: Set[String] = prefNodes.map(_.id).toSet

        val lineByNode: Map[String, Int] =
          prefNodes.map(n => n.id -> toIntOpt(n.line).getOrElse(Int.MaxValue)).toMap

        // preds from collapsed CFG (only if both ends are in stmtIds)
        val predsM = mutable.Map.empty[String, Set[String]].withDefaultValue(Set.empty)
        absCfg.cfgEdges.foreach { case (s0, t0) =>
          val s = pref(s0); val t = pref(t0)
          if (stmtIds.contains(s) && stmtIds.contains(t)) predsM(t) = predsM(t) + s
        }
        val preds: Map[String, Set[String]] = predsM.toMap.withDefaultValue(Set.empty)

        // fields tracked if they appear as this.<field> somewhere in this method
        val declaredFields: Set[String] = m.typeDecl.member.name.l.toSet
        val usedFields: Set[String] =
          absCfg.nodes.flatMap(n => thisFieldRx.findAllMatchIn(n.code).map(_.group(1))).toSet
        val fieldsTracked: Set[String] = usedFields.intersect(declaredFields)

        val paramsTracked: Set[String] =
          m.parameter.orderGt(0).name.l.map(_.trim).filter(_.nonEmpty).toSet

        // def/use sets
        val defVarsM = mutable.Map.empty[String, Set[String]].withDefaultValue(Set.empty)
        val useVarsM = mutable.Map.empty[String, Set[String]].withDefaultValue(Set.empty)

        prefNodes.foreach { n =>
          val (defs, uses) = extractDefsUses(n.code, fieldsTracked, paramsTracked)
          defVarsM(n.id) = defs
          useVarsM(n.id) = uses
        }

        methodInfos += MethodInfo(
          methodFull      = mFull,
          owner           = owner,
          typeKey         = typeKey,
          pfx             = pfx,
          bodyDotStripped = body,
          stmtIds         = stmtIds,
          lineByNode      = lineByNode,
          preds           = preds,
          defVars         = defVarsM.toMap.withDefaultValue(Set.empty),
          useVars         = useVarsM.toMap.withDefaultValue(Set.empty),
          fieldsTracked   = fieldsTracked,
          paramsTracked   = paramsTracked,
          isConstructor   = isCtor(m)
        )
      }
    }

    // (A) Constructor seeds per type: last def in <init> for each SV::<field>
    val initSeedsByType: Map[String, Map[String, String]] = {
      methodInfos.toList.groupBy(_.typeKey).map { case (typeKey, infos) =>
        val lastDefByVar = mutable.Map.empty[String, (Int, String)] // var -> (line, defNodeId)
        infos.filter(_.isConstructor).foreach { initInfo =>
          initInfo.stmtIds.foreach { nid =>
            val ln = initInfo.lineByNode.getOrElse(nid, Int.MaxValue)
            initInfo.defVars.getOrElse(nid, Set.empty).foreach { v =>
              if (v.startsWith("SV::")) {
                lastDefByVar.get(v) match {
                  case Some((oldLn, _)) if oldLn >= ln => // keep old
                  case _ => lastDefByVar.update(v, (ln, nid))
                }
              }
            }
          }
        }
        typeKey -> lastDefByVar.toMap.view.mapValues(_._2).toMap
      }
    }

    // nodeId -> methodFull (to block backwards deps only within same method)
    val nodeToMethodFull: Map[String, String] =
      methodInfos.toList.flatMap(mi => mi.stmtIds.map(id => id -> mi.methodFull)).toMap

    // (B) Data dependency edges via reaching definitions (+ constructor seeding)
    type DefPair = (String, String) // (varKey, defNodeId)
    val dataDepEdges = mutable.Set.empty[String]

    methodInfos.foreach { mi =>
      val stmtIds = mi.stmtIds
      if (stmtIds.nonEmpty) {

        // IMPORTANT FIX: seed at real CFG start nodes (nodes with no preds)
        val sources = stmtIds.filter(id => mi.preds.getOrElse(id, Set.empty).isEmpty)
        val startNodes =
          if (sources.nonEmpty) sources
          else Set(stmtIds.minBy(id => mi.lineByNode.getOrElse(id, Int.MaxValue)))

        // Seed parameters at "entry" (we approximate by seeding startNodes)
        val paramSeeds: Set[DefPair] =
          mi.paramsTracked.map(p => (s"P::$p", startNodes.head)) // def node id for params not important; we keep it distinct below

        // Seed state vars from constructor last defs (skip for constructors themselves)
        val initSeedMap = initSeedsByType.getOrElse(mi.typeKey, Map.empty)
        val svSeeds: Set[DefPair] =
          if (mi.isConstructor) Set.empty
          else mi.fieldsTracked.flatMap { f =>
            val v = s"SV::$f"
            initSeedMap.get(v).map(defNodeId => (v, defNodeId))
          }

        // We do NOT want param seed "def nodes" to show as edges; instead, treat params as defined at startNodes themselves:
        val paramSeedsAtStart: Set[DefPair] =
          startNodes.flatMap { start =>
            mi.paramsTracked.map(p => (s"P::$p", start))
          }

        val initialDefs: Set[DefPair] = paramSeedsAtStart ++ svSeeds

        def gen(nodeId: String): Set[DefPair] =
          mi.defVars.getOrElse(nodeId, Set.empty).map(v => (v, nodeId))

        // IN / OUT maps
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

        // Emit data deps: def reaches use
        for (useNodeId <- stmtIds; v <- mi.useVars.getOrElse(useNodeId, Set.empty)) {
          val reachingDefs: Set[String] =
            inMap.getOrElse(useNodeId, Set.empty).collect { case (`v`, defId) => defId }

          reachingDefs.foreach { defId =>
            if (defId != useNodeId) {
              val defMeth = nodeToMethodFull.getOrElse(defId, "")
              val useMeth = nodeToMethodFull.getOrElse(useNodeId, "")

              // block backward deps ONLY within the same method
              val ok =
                if (defMeth.nonEmpty && defMeth == useMeth) {
                  val dl = mi.lineByNode.getOrElse(defId, Int.MaxValue)
                  val ul = mi.lineByNode.getOrElse(useNodeId, Int.MaxValue)
                  dl < ul
                } else true // allow <init> -> other-method deps

              if (ok) {
                dataDepEdges += s""""$defId" -> "$useNodeId" [label="data dep", color="purple"]"""
              }
            }
          }
        }
      }
    }

    // Cluster by owner/class
    val clustersByClass: List[String] =
      methodInfos.toList.groupBy(_.owner).toList.sortBy(_._1).flatMap { case (owner, infos) =>
        val ownerSan = owner.replaceAll("[^A-Za-z0-9_]", "_")
        val bodies   = infos.map(_.bodyDotStripped)
        if (bodies.isEmpty) None
        else Some(
          s"""subgraph "cluster_$ownerSan" {
  label="${html(owner)}";
  ${bodies.mkString("\n")}
}"""
        )
      }

    val out =
      s"""digraph "program_cfg_abstract_by_class" {
compound=true;
node [shape="rect"];
${clustersByClass.mkString("\n\n")}

${dataDepEdges.toList.sorted.mkString("\n")}
}"""

    println(out)
    out
  }
}

// Run
val dot = CfgByClass.run(".*AB_StaticEquivalent\\.java")
